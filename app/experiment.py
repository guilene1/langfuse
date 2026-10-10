"""The 22 test questions as a Langfuse dataset, run as experiments and scored.

    python -m app.experiment setup                 score configs and the dataset (safe to repeat)
    python -m app.experiment score-traces RUN      golden_check scores on traces app.evaluate recorded
    python -m app.experiment judges [--live-sampling 0.2]   the faithfulness, citation and relevance judges
    python -m app.experiment run NAME [--judge] [--limit N] [--min-similarity X]
    python -m app.experiment review-queue [--run baseline]   answers for a person to grade

The test logic is evaluate.py's, called unchanged: run_case asks the question (and any earlier
turns of a follow-up), and check decides pass or fail. data/evaluation.json stays the one place
the test cases are written; `setup` copies it into Langfuse.
"""
import argparse
import datetime
import json
import os
import time
from statistics import mean

from langfuse import Evaluation
from langfuse.api import (
    AnnotationQueueObjectType,
    ConfigCategory,
    CreateEvaluatorRequest_LlmAsJudge,
    EvaluationRuleEvaluatorAssignmentInput,
    EvaluationRuleFilter_StringOptions,
    EvaluatorModelConfig,
    EvaluatorOutputDefinition_Boolean,
    EvaluatorOutputDefinition_Numeric,
    LlmAdapter,
    NotFoundError,
    PromptVariableMappingInput,
    PromptVariableMappingSource,
    UpdateLlmAsJudgeEvaluatorRequest,
)

from app import config, evaluate, judges, tracing

DATASET = "northwind-golden"

# The fields of a test case that say what a passing answer looks like
CHECK_FIELDS = ["must_contain", "must_not_contain", "any_of", "must_disclaim", "expect_no_sources"]

# Every score the lab records, defined once so the UI shows each one consistently
SCORE_CONFIGS = [
    {"name": "golden_check", "data_type": "BOOLEAN",
     "description": "The test case's own checks from data/evaluation.json passed"},
    {"name": "correctness", "data_type": "NUMERIC", "min_value": 0, "max_value": 1,
     "description": "LLM judge: 1 correct, 0.5 partially correct, 0 incorrect, against the test case"},
    {"name": "faithfulness", "data_type": "NUMERIC", "min_value": 0, "max_value": 1,
     "description": "LLM judge: every claim is supported by the excerpts Claude was given"},
    {"name": "citation_accuracy", "data_type": "BOOLEAN",
     "description": "LLM judge: every [n] points at an excerpt containing that fact"},
    {"name": "relevance", "data_type": "BOOLEAN",
     "description": "LLM judge: the answer addresses the question asked"},
    {"name": "human_correctness", "data_type": "CATEGORICAL",
     "categories": [("incorrect", 0), ("partially correct", 0.5), ("correct", 1)],
     "description": "A person's verdict, given in the Langfuse UI"},
    {"name": "user_feedback", "data_type": "BOOLEAN",
     "description": "Thumbs up or down from the chat page"},
    {"name": "pass_rate", "data_type": "NUMERIC", "min_value": 0, "max_value": 1,
     "description": "Share of test cases in an experiment run whose golden_check passed"},
]

langfuse = tracing.langfuse


# --- Setup -------------------------------------------------------------------------------

def ensure_score_configs():
    """Creates any missing score config. Returns {name: config id}."""
    existing = {c.name: c.id for c in langfuse.api.score_configs.get(limit=100).data if not c.is_archived}
    for spec in SCORE_CONFIGS:
        if spec["name"] in existing:
            continue
        # Langfuse rejects a field sent as null, so only the ones this config uses are sent
        optional = {key: spec[key] for key in ("min_value", "max_value") if key in spec}
        if "categories" in spec:
            optional["categories"] = [ConfigCategory(label=label, value=value) for label, value in spec["categories"]]
        created = langfuse.api.score_configs.create(
            name=spec["name"], data_type=spec["data_type"], description=spec["description"], **optional)
        existing[spec["name"]] = created.id
    return existing


def sync_dataset():
    """Copies data/evaluation.json into the Langfuse dataset. Repeating it updates, never duplicates."""
    try:
        langfuse.get_dataset(DATASET)
    except NotFoundError:
        langfuse.create_dataset(
            name=DATASET,
            description="Northwind HR assistant: the 22 test questions from data/evaluation.json",
        )
    cases = evaluate.load_cases()
    for case in cases:
        langfuse.create_dataset_item(
            dataset_name=DATASET,
            # The case id as the item id: syncing again updates this item instead of adding one
            id=case["id"],
            input={"question": case["question"], "after": case.get("after", [])},
            # "why" describes what a good answer does, so it sits with the expectations. (Item
            # metadata is also copied onto every trace, where values over 200 characters are dropped.)
            expected_output={field: case[field] for field in CHECK_FIELDS + ["why"] if field in case},
            metadata={"case_id": case["id"], "trap": case["trap"]},
        )
    return len(cases)


def case_from_item(input, expected_output, metadata):
    # Puts a dataset item back into the shape evaluate.py works with
    return {"id": metadata["case_id"], "trap": metadata["trap"],
            "question": input["question"], "after": input.get("after", []), **(expected_output or {})}


# --- The experiment: task and evaluators -------------------------------------------------

def task(*, item, **kwargs):
    # Asks the case's question the same way app.evaluate does, earlier turns included
    case = case_from_item(item.input, item.expected_output, item.metadata)
    answer, _failures = evaluate.run_case(case)
    return answer


def golden_check(*, input, output, expected_output, metadata, **kwargs):
    # evaluate.check, unchanged: the same pass or fail as `python -m app.evaluate`
    failures = evaluate.check(case_from_item(input, expected_output, metadata), output)
    return Evaluation(name="golden_check", value=0 if failures else 1, data_type="BOOLEAN",
                      comment="; ".join(failures) or "all checks passed")


def correctness(*, input, output, expected_output, metadata, **kwargs):
    verdict = judges.judge_correctness(case_from_item(input, expected_output, metadata), output)
    return Evaluation(name="correctness", value=verdict["score"], comment=verdict["reasoning"],
                      metadata={"verdict": verdict["verdict"], "judge_model": judges.JUDGE_MODEL})


def pass_rate(*, item_results, **kwargs):
    results = [e.value for r in item_results for e in r.evaluations if e.name == "golden_check"]
    return Evaluation(name="pass_rate", value=mean(results) if results else 0,
                      comment=f"{sum(results)} of {len(results)} test cases passed")


def run(name, *, judge=False, limit=None, min_similarity=None, description=None):
    """Runs the dataset through the assistant as one experiment run, and scores it.

    min_similarity changes the retrieval cut-off for this run only, to show what a regression
    looks like. The original value is put back afterwards, even if the run fails.
    """
    original = config.MIN_BEST_SIMILARITY
    if min_similarity is not None:
        config.MIN_BEST_SIMILARITY = min_similarity
    try:
        items = langfuse.get_dataset(DATASET).items
        evaluators = [golden_check] + ([correctness] if judge else [])
        result = langfuse.run_experiment(
            name=name,
            description=description or f"Northwind golden set, cut-off {config.MIN_BEST_SIMILARITY}",
            data=items[:limit] if limit else items,
            task=task,
            evaluators=evaluators,
            run_evaluators=[pass_rate],
            # A few at a time: fast, and well inside the Claude API's rate limits
            max_concurrency=4,
            metadata={"min_best_similarity": str(config.MIN_BEST_SIMILARITY),
                      "claude_model": config.CLAUDE_MODEL,
                      "judge_model": judges.JUDGE_MODEL if judge else "none"},
        )
    finally:
        config.MIN_BEST_SIMILARITY = original
    tracing.flush()
    return result


def summarise(result):
    """Prints a short report of an experiment run and returns it as a dict."""
    rows = []
    for item_result in result.item_results:
        scores = {e.name: e.value for e in item_result.evaluations}
        comments = {e.name: e.comment for e in item_result.evaluations}
        rows.append({"case_id": item_result.item.metadata["case_id"],
                     "trap": item_result.item.metadata["trap"],
                     "trace_id": item_result.trace_id, **scores,
                     "failures": comments.get("golden_check"),
                     "judge_reasoning": comments.get("correctness")})
    passed = sum(r.get("golden_check", 0) for r in rows)
    print(f"{result.run_name}: {passed}/{len(rows)} passed")
    if any("correctness" in r for r in rows):
        print(f"  correctness (judge) average: {mean(r['correctness'] for r in rows if 'correctness' in r):.2f}")
    for r in rows:
        if not r.get("golden_check"):
            print(f"  FAIL {r['case_id']} [{r['trap']}]: {r['failures']}")
        if r.get("correctness", 1) < 1:
            print(f"  judge {r['correctness']} {r['case_id']} [{r['trap']}]: {r['judge_reasoning']}")
    if result.dataset_run_url:
        # The SDK builds the link from LANGFUSE_BASE_URL, which is the container's name for Langfuse
        print("  open:", result.dataset_run_url.replace("host.docker.internal", "localhost"))
    return rows


# --- Scoring traces that already exist ---------------------------------------------------

def score_existing_traces(eval_run):
    """Adds golden_check scores to the traces one `python -m app.evaluate` run recorded.

    Nothing is asked again and Claude is not called: each trace already stores the answer, and
    evaluate.check grades it. Running this twice overwrites the same scores.
    """
    config_ids = ensure_score_configs()
    cases = {case["id"]: case for case in evaluate.load_cases()}
    roots = langfuse.api.observations.get_many(
        is_root_observation=True, fields="core,basic,io,metadata", limit=1000).data
    # A follow-up case records one trace per turn. The case is judged on the last one.
    final_turn = {}
    for root in roots:
        meta = root.metadata or {}
        if meta.get("eval_run") != eval_run or meta.get("case_id") not in cases:
            continue
        if meta["case_id"] not in final_turn or root.start_time > final_turn[meta["case_id"]].start_time:
            final_turn[meta["case_id"]] = root
    results = {}
    for case_id, root in final_turn.items():
        answer = json.loads(root.output) if isinstance(root.output, str) else root.output
        failures = evaluate.check(cases[case_id], answer)
        tracing.score(
            # A fixed id per trace, so scoring again replaces the score instead of adding one
            score_id=f"golden-check-{root.trace_id}",
            trace_id=root.trace_id, name="golden_check", value=0 if failures else 1,
            data_type="BOOLEAN", config_id=config_ids["golden_check"],
            comment="; ".join(failures) or "all checks passed",
            metadata={"eval_run": eval_run, "case_id": case_id},
        )
        results[case_id] = not failures
    tracing.flush()
    return results


# --- Human review ------------------------------------------------------------------------

def latest_experiment(prefix):
    """The most recent experiment run whose name starts with prefix, e.g. "baseline"."""
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
    runs = [e for e in langfuse.api.experiments.list(from_start_time=since, limit=100).data
            if e.name.startswith(prefix)]
    if not runs:
        raise SystemExit(f"no experiment run starting with {prefix!r} in the last 30 days")
    return max(runs, key=lambda e: e.start_time)


def experiment_traces(experiment):
    """{case id: trace id} for one experiment run."""
    since = experiment.start_time - datetime.timedelta(minutes=1)
    items = langfuse.api.experiments.list_items(from_start_time=since, experiment_id=experiment.id, limit=100).data
    return {item.experiment_item_id: item.trace_id for item in items}


SCORE_NAMES = ["golden_check", "correctness", "faithfulness", "citation_accuracy", "relevance",
               "human_correctness", "user_feedback"]


def experiment_scores(prefix, wait_for_judges=True, timeout=300):
    """{case id: {"trace_id": ..., score name: value}} for the latest run named prefix...

    The judges inside Langfuse score a few seconds after each answer arrives, so by default this
    waits until every answer Claude wrote has its faithfulness score. True/False become 1/0.
    """
    traces = experiment_traces(latest_experiment(prefix))
    answered = {case_id for case_id, trace_id in traces.items()
                if langfuse.api.observations.get_many(trace_id=trace_id, name="claude-answer",
                                                      fields="core", limit=1).data}
    waited = 0
    while True:
        table = {}
        for case_id, trace_id in traces.items():
            row = {"trace_id": trace_id}
            for s in langfuse.api.scores_v3.get_many_v3(trace_id=trace_id, fields="details", limit=50).data:
                if s.name in SCORE_NAMES:
                    row[s.name] = float(s.value) if isinstance(s.value, (bool, int, float)) else s.value
            table[case_id] = row
        judged = {c for c, row in table.items() if "faithfulness" in row}
        # Scores reach Langfuse a moment after they are sent, the checks' as much as the judges'
        checked = all("golden_check" in row for row in table.values())
        if not wait_for_judges or (checked and answered <= judged) or waited >= timeout:
            return table
        time.sleep(5)
        waited += 5


def experiment_costs(prefix):
    """What one experiment run cost, in dollars, as Langfuse priced it."""
    run = latest_experiment(prefix)
    trace_ids = set(experiment_traces(run).values())
    costs = {"answers": 0.0, "correctness judge": 0.0, "Langfuse judges": 0.0}
    for trace_id in trace_ids:
        for g in langfuse.api.observations.get_many(trace_id=trace_id, type="GENERATION",
                                                    fields="core,basic,usage", limit=50).data:
            costs["answers" if g.name == "claude-answer" else "correctness judge"] += g.total_cost or 0
    # The judges inside Langfuse record each call as a trace of its own, in this environment, with
    # the trace it judged in target_trace_id
    end = (run.end_time or datetime.datetime.now(datetime.timezone.utc)) + datetime.timedelta(minutes=10)
    for g in langfuse.api.observations.get_many(environment="langfuse-llm-as-a-judge", type="GENERATION",
                                                from_start_time=run.start_time, to_start_time=end,
                                                fields="core,basic,usage,metadata", limit=1000).data:
        if (g.metadata or {}).get("target_trace_id") in trace_ids:
            costs["Langfuse judges"] += g.total_cost or 0
    return {name: round(value, 4) for name, value in costs.items()}


def create_review_queue(prefix="baseline", size=8):
    """An annotation queue of answers for a person to grade with human_correctness.

    The answers a judge or a check marked down come first, because that is where a person's
    verdict says the most about whether to trust the judge. Returns (queue id, case ids).
    """
    config_ids = ensure_score_configs()
    traces = experiment_traces(latest_experiment(prefix))
    traps = {case["id"]: case["trap"] for case in evaluate.load_cases()}

    def lowest_score(trace_id):
        values = [s.value for s in langfuse.api.scores_v3.get_many_v3(trace_id=trace_id, limit=50).data
                  if s.name in ("golden_check", "correctness", "faithfulness", "citation_accuracy", "relevance")]
        return min((float(v) for v in values), default=1.0)

    ranked = sorted(traces, key=lambda case_id: (lowest_score(traces[case_id]), traps[case_id] == "none"))
    chosen = ranked[:size]
    name = f"Northwind review ({prefix})"
    # Reuse the queue if it exists, so running this again does not make a second one
    queue = next((q for q in langfuse.api.annotation_queues.list_queues(limit=100).data if q.name == name), None)
    if queue is None:
        queue = langfuse.api.annotation_queues.create_queue(
            name=name,
            description="Grade each answer yourself: correct, partially correct or incorrect. "
                        "Then compare your verdicts with the judges' scores on the same traces.",
            score_config_ids=[config_ids["human_correctness"]],
        )
    queued = {item.object_id for item in langfuse.api.annotation_queues.list_queue_items(queue.id, limit=100).data}
    for case_id in chosen:
        if traces[case_id] not in queued:
            langfuse.api.annotation_queues.create_queue_item(
                queue.id, object_id=traces[case_id], object_type=AnnotationQueueObjectType.TRACE)
    return queue.id, chosen


# --- The judges that run inside Langfuse -------------------------------------------------

def _judge_definition(judge, model):
    output_type = (EvaluatorOutputDefinition_Numeric if judge["data_type"] == "NUMERIC"
                   else EvaluatorOutputDefinition_Boolean)
    output = dict(score_reasoning_instructions=judge["reasoning_instructions"],
                  score_value_instructions=judge["value_instructions"])
    if judge["data_type"] == "NUMERIC":
        output.update(min_value=0.0, max_value=1.0)
    return dict(
        name=judge["name"],
        description=f"Northwind lab: {judge['name']} of a claude-answer observation",
        prompt=judge["prompt"],
        model_config_=EvaluatorModelConfig(provider="anthropic", model=model),
        variable_mapping=[
            # Only the messages, not the system prompt: the excerpts and question are all a judge needs
            PromptVariableMappingInput(variable="context", source=PromptVariableMappingSource.INPUT,
                                       json_path="$.messages"),
            PromptVariableMappingInput(variable="answer", source=PromptVariableMappingSource.OUTPUT),
        ],
        output_definition=output_type(**output),
    )


def _strings(value):
    # Every string anywhere inside a nested dict or list
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _judge_changed(evaluator, judge, model):
    # Compares what is stored in Langfuse with judges.py, so running `judges` again is harmless
    stored = set(_strings(langfuse.api.evaluators.get(evaluator.id).dict()))
    return not {judge["prompt"], judge["reasoning_instructions"], model} <= stored


def setup_managed_judges(model=None, anthropic_key=None):
    """Creates the LLM connection, the three judges and the rule that runs them, if missing.

    The rule runs every judge on every new `claude-answer` observation: chat answers and
    experiment answers alike. Returns {judge name: evaluator id}.
    """
    model = model or judges.JUDGE_MODEL
    if not any(c.provider == "anthropic" for c in langfuse.api.llm_connections.list().data):
        langfuse.api.llm_connections.upsert(
            provider="anthropic", adapter=LlmAdapter.ANTHROPIC,
            secret_key=anthropic_key or os.environ["ANTHROPIC_API_KEY"],
            with_default_models=True, custom_models=[model],
        )
    existing = {e.name: e for e in langfuse.api.evaluators.list().data}
    evaluator_ids = {}
    for judge in judges.MANAGED_JUDGES:
        definition = _judge_definition(judge, model)
        current = existing.get(judge["name"])
        if current is None:
            current = langfuse.api.evaluators.create(request=CreateEvaluatorRequest_LlmAsJudge(**definition))
        elif _judge_changed(current, judge, model):
            # Saved as a new version of the same judge; the rule always runs the latest version
            current = langfuse.api.evaluators.update(
                current.id, request=UpdateLlmAsJudgeEvaluatorRequest(**definition))
        evaluator_ids[judge["name"]] = current.id

    if not any(r.name == "Judge claude-answer" for r in langfuse.api.evaluation_rules.list().data):
        langfuse.api.evaluation_rules.create(
            name="Judge claude-answer",
            enabled=True,
            # 1 means every answer is judged. Lower it (0.1 is one in ten) to cut cost on busy traffic.
            sampling=1.0,
            filter=[EvaluationRuleFilter_StringOptions(column="name", operator="any of",
                                                       value=["claude-answer"])],
            evaluator_assignments=[EvaluationRuleEvaluatorAssignmentInput(evaluator_id=i)
                                   for i in evaluator_ids.values()],
        )
    return evaluator_ids


def set_judge_sampling(live_sampling):
    """Replaces the one rule that judges every answer with one rule per environment.

    Live chats (environment default) are judged at live_sampling, 0.2 meaning one answer in
    five. Experiments and alert drills stay at 1: an experiment is a measurement, and a sampled
    one could not be compared with the last, and a drill must trip its alert every time.
    The original rule is switched off, not deleted.
    """
    evaluators = {e.name: e.id for e in langfuse.api.evaluators.list().data}
    assignments = [EvaluationRuleEvaluatorAssignmentInput(evaluator_id=evaluators[j["name"]])
                   for j in judges.MANAGED_JUDGES]
    rules = {r.name: r for r in langfuse.api.evaluation_rules.list().data}
    wanted = [("Judge live answers", "default", live_sampling),
              ("Judge experiments", "sdk-experiment", 1.0),
              ("Judge alert drills", "alert-drill", 1.0)]
    for name, environment, sampling in wanted:
        filters = [EvaluationRuleFilter_StringOptions(column="name", operator="any of", value=["claude-answer"]),
                   EvaluationRuleFilter_StringOptions(column="environment", operator="any of", value=[environment])]
        if name in rules:
            langfuse.api.evaluation_rules.update(rules[name].id, sampling=sampling, filter=filters, enabled=True)
        else:
            langfuse.api.evaluation_rules.create(name=name, enabled=True, sampling=sampling, filter=filters,
                                                 evaluator_assignments=assignments)
    if "Judge claude-answer" in rules and rules["Judge claude-answer"].enabled:
        langfuse.api.evaluation_rules.update(rules["Judge claude-answer"].id, enabled=False)
    return {r.name: (r.enabled, r.sampling) for r in langfuse.api.evaluation_rules.list().data}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    score_traces = commands.add_parser("score-traces")
    score_traces.add_argument("eval_run", help="the run id app.evaluate printed in metadata, e.g. 20261010-001108")
    judges_parser = commands.add_parser("judges")
    judges_parser.add_argument("--live-sampling", type=float, metavar="FRACTION",
                               help="judge this share of live chat answers (e.g. 0.2); experiments and drills stay at 1")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("name")
    run_parser.add_argument("--judge", action="store_true", help="also run the correctness judge")
    run_parser.add_argument("--limit", type=int, help="only the first N test cases")
    run_parser.add_argument("--min-similarity", type=float, help="retrieval cut-off for this run only")
    queue_parser = commands.add_parser("review-queue")
    queue_parser.add_argument("--run", default="baseline", help="experiment name prefix (default: baseline)")
    queue_parser.add_argument("--size", type=int, default=8)
    args = parser.parse_args()

    if not tracing.ENABLED:
        raise SystemExit("Langfuse keys are not set, so there is nowhere to store datasets or scores.")
    if args.command == "setup":
        print("score configs:", sorted(ensure_score_configs()))
        print("dataset items:", sync_dataset())
    elif args.command == "score-traces":
        results = score_existing_traces(args.eval_run)
        print(f"scored {len(results)} traces: {sum(results.values())} passed")
    elif args.command == "judges":
        print("managed judges:", setup_managed_judges())
        if args.live_sampling is not None:
            print("rules (enabled, sampling):", set_judge_sampling(args.live_sampling))
    elif args.command == "run":
        summarise(run(args.name, judge=args.judge, limit=args.limit, min_similarity=args.min_similarity))
    elif args.command == "review-queue":
        queue_id, cases = create_review_queue(args.run, args.size)
        print(f"annotation queue {queue_id} with {len(cases)} answers: {', '.join(cases)}")


if __name__ == "__main__":
    main()
