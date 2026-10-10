# Part 3: Grade the answers with scores, judges and experiments

In Part 2 every question became a trace you could read. Reading traces tells you what happened to one
answer. This part tells you **how good the answers are**, in numbers you can compare from one change
to the next.

**Time:** about 60 minutes. **Claude cost:** about 15 cents for the whole part.

The hands-on steps are in the notebook `notebooks/03-evaluation-scoring.ipynb`. This guide explains
them: read a section here, then run the matching part of the notebook.

## Words you will see

| Word | Meaning here |
|---|---|
| **Score** | A grade attached to a trace or to one observation, such as `golden_check = true` or `faithfulness = 0.5`. It can carry a comment explaining it. |
| **Score config** | The definition of a score: its name, its type (true/false, a number from 0 to 1, or a category), and what it means |
| **Dataset** | A fixed list of test inputs, each with what a good answer must do |
| **Experiment** | One run of the assistant over a dataset, with every answer graded. Two experiments on the same dataset can be compared. |
| **Evaluator** | Something that grades an answer: a function in your code, or a judge inside Langfuse |
| **LLM-as-a-judge** | A language model that grades another model's answer against written criteria |
| **Rule** | Tells Langfuse which observations its judges should grade, and how many of them |
| **Annotation queue** | A to-do list of traces for people to grade |

## Four ways to grade an answer

| Who grades | What it is good at | Cost | Score names |
|---|---|---|---|
| **The test checks** in `evaluate.py` | Exact and free, but only sees words: "25" in the answer passes even if the rest is wrong | none | `golden_check` |
| **An LLM judge in our code** | Reads for meaning, and knows what each test case expects | ~$0.0002 per answer | `correctness` |
| **LLM judges inside Langfuse** | Grade every answer automatically, including live chats, without any code in the app | ~$0.0003 per answer per judge | `faithfulness`, `citation_accuracy`, `relevance` |
| **People** | Decide whether the judges can be trusted | your time | `human_correctness` |

There is also `user_feedback`, from the 👍 and 👎 under each answer in the chat page, and `pass_rate`,
the share of test cases that passed in one experiment.

### Why the judges sit where they do

A judge inside Langfuse sees **one observation**, nothing else. The `claude-answer` observation is the
only one holding both the numbered excerpts Claude was given and the answer it wrote, so that is where
the faithfulness, citation and relevance judges run.

`correctness` is different: to know whether an answer is correct, the judge needs the test case's
expectations ("must say 5 days and 31 March, must not say 10 days"). Only the dataset has those, so
that judge runs in our code during an experiment, in `app/judges.py`.

## Setup

Start JupyterLab, once:

```powershell
docker compose up -d --no-deps notebook
docker compose logs notebook
```

Near the end of the log is a line like
`http://127.0.0.1:8888/lab?token=3f1c...`. Open that link, then `03-evaluation-scoring.ipynb`.

The notebook runs in a container built from the same image as the chat page, with the same code
mounted, so it imports the real assistant: `from app import experiment`. Nothing in the notebook
copies the assistant's logic.

Everything the notebook does is also a command, for scripts and CI:

```powershell
docker compose exec app python -m app.experiment setup                  # score configs and dataset
docker compose exec app python -m app.experiment score-traces 20261010-001108
docker compose exec app python -m app.experiment judges                 # the judges inside Langfuse
docker compose exec app python -m app.experiment run baseline --judge
docker compose exec app python -m app.experiment run tighter --judge --min-similarity 0.77
docker compose exec app python -m app.experiment review-queue
```

## Part A: Score traces you already have

Every trace from `python -m app.evaluate` stores the answer and, in its metadata, the case id and the
run id (`eval_run`). So you can grade them again without asking Claude anything: the notebook runs
`evaluate.check` on each stored answer and attaches the result as a `golden_check` score.

**In Langfuse:** **Tracing** → open a trace tagged `evaluation` → its scores. **Scores** in the sidebar
lists every score in the project.

Grading the same traces again replaces their scores rather than adding more: each score has a fixed id,
`golden-check-<trace id>`.

## Part B: The dataset

`data/evaluation.json` stays the one place the 22 test cases are written. The notebook copies it into a
Langfuse dataset named `northwind-golden`:

| Dataset item | Holds |
|---|---|
| id | the case id, such as `carryover-superseded`. Copying again updates the item instead of adding one. |
| input | the question, and for a follow-up the questions asked before it |
| expected output | the checks (`must_contain`, `must_not_contain`, ...) and `why`, which explains the trap |
| metadata | the case id and the trap |

**In Langfuse:** **Datasets** → `northwind-golden`.

The judges inside Langfuse are created here too, along with the Anthropic connection they use. See
[The judges inside Langfuse](#the-judges-inside-langfuse).

## Part C: A baseline experiment

The experiment runs each dataset item through `evaluate.run_case`, exactly as `python -m app.evaluate`
does (follow-ups replay their earlier questions first), and grades each answer with:

- `golden_check`: `evaluate.check`, unchanged
- `correctness`: the judge in `app/judges.py`
- `faithfulness`, `citation_accuracy`, `relevance`: the judges inside Langfuse, which grade each
  `claude-answer` a few seconds after it arrives

**In Langfuse:** **Experiments** → the baseline run. Each row is a test case with its scores. Judge calls
made inside Langfuse have their own traces, in the `langfuse-llm-as-a-judge` environment: choose that
environment in **Tracing** to see them.

### What to expect

From the runs made while building this lab:

| Score | Baseline (cut-off 0.595) |
|---|---|
| golden_check | 22 of 22 |
| correctness | 0.98 |
| faithfulness | 0.90 to 0.92 |
| citation_accuracy | 0.90 to 1.00 |
| relevance | 1.00 |

Your numbers will differ a little: Claude does not write the same answer twice, and neither do the judges.

### Where the judges disagree with the checks

These are the most useful rows. Examples seen while building the lab:

- **personal-review-date**: the check passed. The correctness judge gave 0.5: the answer quoted the
  *salary* review date (1 April) from the payroll policy for a question about *performance* reviews.
- **pto-entitlement**: the check passed, because "25" was there. Faithfulness gave 0: the answer said
  first-year accrual applies to part-time staff, while the policy applies it to everyone.
- **sick-pay-scoped**: faithfulness gave 0 for "full salary for 6 weeks" in Germany, where the excerpt
  says "continued payment of salary". Strict, but the word "full" is not in the source.

And examples where a judge was arguably **too** strict: Juneteenth lost half a point for saying
"at Northwind Systems". Judges are useful, not infallible. That is what Part E is for.

## Part D: Your own evaluator

An evaluator is a function that receives `input`, `output`, `expected_output` and `metadata`, and returns
an `Evaluation(name, value, comment)`. The notebook has you write one that checks whether an answer that
found no policy still tells the person where to go next. It costs nothing to run, and the first result
raises a real question: is a greeting that points nowhere a failure?

## Part E: Grade answers yourself

1. The notebook puts eight answers in a queue, the ones a check or a judge marked down first.
2. **In Langfuse:** **Human Annotation** → `Northwind review (baseline)`.
3. For each answer, read the question, the answer and its sources, set `human_correctness` to
   *correct*, *partially correct* or *incorrect*, and go to the next.
4. Back in the notebook, compare your grades with the judges'.

Any trace can also be graded on its own: open it in **Tracing** and annotate it.

Where you and a judge disagree, read the judge's reasoning. If it is wrong in the same way more than
once, change its prompt (`app/judges.py`) and run `python -m app.experiment judges` again: the judge
gets a new version, and the rule always uses the latest.

**The 👍 and 👎 in the chat page** are human scores from users rather than reviewers. Each click records
`user_feedback` on that answer's trace; changing your mind replaces it. Only answers given since the
page was opened show them, because the trace id is kept in the browser session, not in the database.

## Part F: Catch a regression

"Does Northwind offer pet insurance?" scores 0.762 against the documents, so the excerpts reach Claude.
Raising the retrieval cut-off to 0.77 would stop that. The notebook tries it for one experiment only.
`config.MIN_BEST_SIMILARITY` is changed in memory, and put back afterwards even if the run fails; no file
changes.

What happened while building the lab:

| | Baseline | Cut-off 0.77 |
|---|---|---|
| golden_check passed | 22 of 22 | 15 of 22 |
| correctness | 0.98 | 0.70 |

Seven real questions, among them five-year holiday allowance, gift approval, German sick notes and UK
maternity pay, now get "I could not find anything". The pet-insurance question was never failing in
the first place.

**In Langfuse:** **Experiments**: select both runs to compare them side by side.

In CI the same comparison becomes a gate: compute the pass rate of the new run, and fail the build if it
dropped. The notebook's last cell shows the idea.

## The judges inside Langfuse

`python -m app.experiment judges` (also run by the notebook) sets up three things, each only if it is
missing:

| What | In Langfuse | Notes |
|---|---|---|
| An Anthropic connection | **Settings** → **LLM Connections** | Uses `ANTHROPIC_API_KEY`. Langfuse stores it encrypted with `ENCRYPTION_KEY` from `langfuse\.env`. |
| Three judges | **Evaluators** | Prompts in `app/judges.py` (`MANAGED_JUDGES`). Each sees `{{context}}`, the messages Claude was sent, and `{{answer}}`, its reply. |
| A rule | **Evaluators** | Runs all three on every new observation named `claude-answer`, in chats and experiments alike. Sampling is 1 (every answer). |

You can build the same judges by hand: **Evaluators** → **New evaluator** → **LLM-as-a-Judge**, paste a
prompt from `app/judges.py`, map `context` to the observation's input (JSON path `$.messages`) and
`answer` to its output, then choose which observations it runs on.

Judges only grade observations that arrive **after** the rule exists. Older ones can be graded in a
batch from the Langfuse UI.

### How the judge prompts are written

- **Instructions first, data last, inside tags** (`<conversation>`, `<answer>`). The first version put
  instructions after the answer. The judge read them as part of the answer it was grading, and one judge
  replied with nothing but "placeholder". Testing on a few answers before a full run caught it.
- **Reasoning before the score.** The reasoning becomes the score's comment, which is what makes a
  disagreement possible to investigate.
- **A fixed scale**: faithfulness 1 / 0.5 / 0; correctness correct / partially correct / incorrect.
- **Checked against planted mistakes.** An answer that quoted the outdated policy (10 days, 30 June), and
  one that invented a remaining balance, were both judged incorrect, with the reason named.

## What it costs, and keeping it low

Measured with Langfuse's own prices:

| One experiment of the 22 cases | Cost |
|---|---|
| The assistant's answers (Claude Haiku 4.5) | $0.045 |
| `correctness` judge, 22 calls | $0.003 |
| Langfuse judges, 3 × 24 calls | $0.020 |
| **Total** | **about $0.07** |

The regression run costs about $0.03, because fewer questions reach Claude.

How it is kept low:

- **The judges use Claude Haiku 5.5**: $0.10 per million tokens read and $0.50 per million written, a tenth
  of the assistant's model. Set `JUDGE_MODEL=claude-haiku-4-5` to use the assistant's model instead.
- **Low effort.** The code judge asks for `effort: low`; on a grading task this size it answers without
  spending tokens on thinking.
- **Judges run only where there is something to judge**: on `claude-answer`, so greetings and "could not
  find anything" replies cost nothing.
- **The free check runs on everything**; the judges add meaning on top.
- **Sampling.** On real traffic, judge one answer in ten or fewer: the rule's `sampling` in
  `app/experiment.py`, or its settings in **Evaluators**.
- **Short reasoning.** Each judge is asked for three sentences at most.

## Older traces that show $0

Langfuse works out a model call's cost **when the call arrives**, from its price table, and never goes
back. If the price table was missing at that moment, the call keeps a cost of $0 for good.

On the machine this lab was built on, the 24 calls of the first traced run (`eval_run` 20261010-000019)
arrived before the worker had loaded the prices. They were kept, not deleted. To leave them out of a
cost view, filter on a later `eval_run` or a later date. A new installation does not have this problem:
`langfuse/docker-compose.override.yml` makes the worker wait until the database is ready (see Part 1).

## Pinned versions

Every package is pinned, so a rebuild next month installs what the lab was tested with:

- `requirements.txt` and `requirements-local.txt` hold exact versions of what the app uses directly.
- `constraints.txt` holds every package installed in the image, including the ones pulled in indirectly.
- The `Dockerfile` installs with `-c constraints.txt`, from `python:3.12.15-slim`.

To change a version, edit `requirements*.txt`, rebuild, and write `constraints.txt` again from what was
installed:

```powershell
docker compose build app
docker run --rm --entrypoint pip northwind-hr-app:latest freeze > constraints.new
```

Then put the four header lines of the old `constraints.txt` at the top of `constraints.new`, replace
`constraints.txt` with it, and rebuild once more to confirm the build still resolves.

## Notes for writing your own code against Langfuse 4

This installation runs Langfuse 4, which stores everything as observations. Some older endpoints are gone:

| To get | Use | Not |
|---|---|---|
| Traces | `api.observations.get_many(is_root_observation=True, ...)` | `api.trace.list` (404) |
| Sessions | `api.observations.get_many(session_id=...)` | `api.sessions.list` (404) |
| Scores | `api.scores_v3.get_many_v3(..., fields="details,subject")` | |
| Experiment runs | `api.experiments.list(from_start_time=...)`, `list_items(...)` | `get_dataset_runs` (not available) |

Ask for the field groups you need: `fields="core,basic,io,metadata,usage"` on observations. Without
`fields`, only ids, names and times come back.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The notebook link asks for a password or token | Use the full link from `docker compose logs notebook`, including `?token=...` |
| Judge scores never appear | Check **Settings** → **LLM Connections** has an Anthropic connection, and the judges exist in **Evaluators**. Then look in **Tracing**, environment `langfuse-llm-as-a-judge`, for failed executions. |
| `faithfulness` is missing on some answers | Normal for answers that never reached Claude (`route` is `small_talk` or `no_policy`) |
| A score's reasoning is empty | It happens occasionally. The score itself is still recorded. |
| Experiment fails with a 429 | Too many calls at once. `max_concurrency` in `experiment.run` is 4; lower it. |
| `Dataset not found` | Run Part B, or `python -m app.experiment setup` |
| Scores look different from the tables here | Expected: answers and judgements vary between runs. Compare runs you made yourself. |
