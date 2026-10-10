import argparse
import json
import time

from app import assistant, config, embeddings, search_index, tracing


CASES_FILE = config.DATA_DIR / "evaluation.json"


def load_cases():
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def check(case, result):
    text = (result.get("answer") or "").lower()
    failures = []

    required = case.get("must_contain", [])

    if required:
        hits = [phrase for phrase in required if phrase.lower() in text]

        if case.get("any_of"):
            if not hits:
                failures.append(f"none of {required} appeared")
        elif len(hits) != len(required):
            missing = [p for p in required if p.lower() not in text]
            failures.append(f"missing {missing}")

    # The answer must say, in some form, that it cannot see personal data. Several
    # phrasings are acceptable, so any one of them satisfies it.
    disclaimers = case.get("must_disclaim", [])

    if disclaimers and not any(phrase.lower() in text for phrase in disclaimers):
        failures.append(
            f"never said it cannot see personal data (expected one of {disclaimers})"
        )

    for forbidden in case.get("must_not_contain", []):
        if forbidden.lower() in text:
            failures.append(f"said {forbidden!r}")

    if case.get("expect_no_sources") and result.get("sources"):
        cited = [source["title"] for source in result["sources"]]
        failures.append(f"cited sources for a question with no answer: {cited}")

    return failures


def run_case(case):
    # A case can be asked partway through a conversation. "after" lists the questions
    # that came first; they are really asked, so the history this case sees is the
    # history a person would have produced.
    history = []

    for earlier in case.get("after", []):
        earlier_answer = assistant.answer(earlier, history)
        history.append({"role": "user", "content": earlier})
        history.append({"role": "assistant", "content": earlier_answer["answer"]})

    answer = assistant.answer(case["question"], history)
    return answer, check(case, answer)


def run_suite():
    cases = load_cases()
    passed = 0
    # Every case is its own Langfuse session, so a follow-up case shows its earlier turns with it
    run = time.strftime("%Y%m%d-%H%M%S")

    for case in cases:
        # One failed API call should cost one case, not the whole run
        try:
            with tracing.trace_attributes(
                session_id=f"eval-{run}-{case['id']}",
                tags=["evaluation", f"trap:{case['trap']}"],
                metadata={"eval_run": run, "case_id": case["id"]},
            ):
                answer, failures = run_case(case)
        except Exception as error:
            print(f"ERROR {case['id']}: {type(error).__name__}: {error}")
            continue

        if failures:
            print(f"FAIL {case['id']}: {'; '.join(failures)}")
            print(f"     question: {case['question']}")
            print(f"     answer:   {answer['answer'][:200]}")
        else:
            passed += 1
            print(f"pass {case['id']}")

    print(f"\n{passed}/{len(cases)} passed")
    # The process ends right after this, so send any traces still buffered
    tracing.flush()
    return passed == len(cases)


def best_similarity(question, history):
    # The raw top score, with no relevance floor applied - this is what the floor
    # itself is measured against, so it cannot use search_policies, which already
    # applies one.
    text = assistant.search_text(question, history)
    vector = embeddings.embed([text])[0]
    chunks = search_index.search(vector)

    return max((chunk["similarity"] for chunk in chunks), default=0.0)


def run_thresholds():
    cases = load_cases()
    groups = {"real question": [], "small talk": [], "no answer in corpus": []}

    for case in cases:
        # search_text only looks at the role and content of earlier USER turns, so
        # there is no need to actually call the model for each "after" question here.
        history = [
            {"role": "user", "content": earlier}
            for earlier in case.get("after", [])
        ]

        score = best_similarity(case["question"], history)

        if case["id"] == "small-talk":
            groups["small talk"].append(score)
        elif case["trap"] == "not in corpus":
            groups["no answer in corpus"].append(score)
        else:
            groups["real question"].append(score)

    lowest_real = min(groups["real question"])
    highest_small_talk = max(groups["small talk"], default=0.0)
    highest_no_answer = max(groups["no answer in corpus"], default=0.0)

    print(f"{'kind':30} best score")
    print(f"{'real question (lowest)':30} {lowest_real:.3f}")
    print(f"{'small talk (highest)':30} {highest_small_talk:.3f}")
    print(f"{'no answer in corpus (highest)':30} {highest_no_answer:.3f}")

    if highest_small_talk >= lowest_real or highest_no_answer >= lowest_real:
        print(
            f"\n{max(highest_small_talk, highest_no_answer):.3f} is ABOVE the lowest "
            "real question, so no threshold separates those two."
        )
    else:
        print(
            f"\nMIN_BEST_SIMILARITY can sit anywhere between {highest_no_answer:.3f} "
            f"and {lowest_real:.3f}."
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--thresholds",
        action="store_true",
        help="measure the relevance floor instead of running the golden set",
    )
    args = parser.parse_args()

    if args.thresholds:
        run_thresholds()
        return

    raise SystemExit(0 if run_suite() else 1)


if __name__ == "__main__":
    main()
