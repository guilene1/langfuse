# Northwind HR Policy Assistant

An internal assistant that answers employees' HR questions from Northwind Systems' own policy
documents. Every answer carries a citation, and when the documents do not cover the question,
the assistant says so instead of guessing.

It runs on a laptop: PostgreSQL with pgvector holds the text, a Streamlit page handles the
chat, and a language model writes the answers. Two containers, one `docker compose up`.

Next to it runs [Langfuse](https://langfuse.com), self-hosted, which records every answer as a
trace, grades the answers with checks, LLM judges and people, and raises an alert when quality,
speed or cost goes wrong. Langfuse is optional: without its keys the assistant works exactly the same.

```
Q: How many days of paid time off do I get each year?
A: 25 days a year for full-time employees, plus public holidays.
   Sources: Time Off Policy (NW-POL-010, v2026.1)

Q: How many days do I have left?
A: I cannot see individual balances. That is in Workday, under Time Off.

Q: What is our policy on office dogs?
A: I could not find anything about that in the HR policies.
   Ask People Operations.
```

## How it works

```
INDEXING    data/documents/  ->  read  ->  split  ->  embed  ->  PostgreSQL + pgvector
            37 documents in six formats, listed in manifest.json

ANSWERING   question  ->  embed  ->  search  ->  drop weak matches  ->  model
                                                                         |
                                                       answer + citations

WATCHING    every answer  ->  Langfuse trace  ->  scores (checks, LLM judges, people, 👍/👎)
                                                       |
                                               monitors  ->  signed webhook  ->  local alert inbox
```

The documents are deliberately messy, because real ones are. There are PDFs, DOCX, CSV, HTML,
TXT and Markdown files. Some answers live in a table rather than in a sentence. Some policies
have a separate version per region. Two of them are the same policy at different dates.

`data/documents/manifest.json` is what makes that manageable. Every document has a
`document_id`, `version`, `status` and `effective_date`, and indexing stops with an error if
the manifest and the folder do not match. That file is how the assistant tells a current
policy from an old one.

## The four problems this has to solve

**1. The search can work and the answer still be wrong.** Two documents here are the same
policy at different dates. One says you can carry over 5 days, the other says 10. Both sound
equally sure of themselves. The search has to leave out the old one, and the tests have to
check that it did.

**2. Vector search never returns nothing.** It always hands back its closest matches, even for
"hello". Without a relevance cut-off, the assistant will summarise whichever policy is
closest to "hello" and look broken. Measure where that cut-off belongs; do not guess it.

**3. "How many days do I have left?" is the hard one.** The search works perfectly here. The
time off policy comes back with a high score, and a model trying to be helpful will produce a
number it has no way of knowing. A cut-off does not catch this. It takes a rule in the prompt
and its own test cases.

**4. An answer you cannot check is not much use.** Every answer names the document, section
and version it came from. The numbers in the text have to match the sources listed
underneath.

And one that only shows up once it is running: **5. These systems get worse without any error
appearing.** A changed prompt, cut-off or model can quietly break answers that used to be right.
That is what the Langfuse part is for: traces to see what happened, scores to measure it, and
alerts so you hear about it.

## What is in the repository

```
data/documents/        37 HR policy documents in six formats, and manifest.json
data/evaluation.json   22 test questions, each with the trap it is checking for
app/                   the application, plus its Langfuse tracing, evaluation and fire drills
docs/build-plan.md     the order to build app/ in, if you write it yourself
docs/langfuse-lab/     the Langfuse lab, in four parts
notebooks/             JupyterLab exercises for the evaluation part of the lab
langfuse/              the self-hosted Langfuse stack and the local alert inbox
Dockerfile             the image the app, the ingestion and the notebook run in
docker-compose.yml     PostgreSQL with pgvector, the app, and JupyterLab
requirements*.txt      the libraries, at exact versions
constraints.txt        every package in the image, pinned, including indirect ones
.env.example           copy to .env and add your model API key, and later your Langfuse keys
```

| Module | What it does |
|---|---|
| `config.py` | Every setting, read from environment variables |
| `documents.py` | Read PDF, DOCX, CSV, HTML, TXT and Markdown into titled sections |
| `chunking.py` | Cut sections into pieces small enough to search |
| `embeddings.py` | Turn text into vectors |
| `search_index.py` | Create the pgvector table, write chunks, search it |
| `ingest.py` | Run the pipeline, checking the manifest before anything else |
| `retrieval.py` | Embed the question, search, drop the weak matches |
| `assistant.py` | Give the model the text and the rules, get an answer with citations |
| `conversations.py` | Store chats in Postgres so a refresh does not lose them |
| `main.py` | The Streamlit page: a chat box, the answers, the sources, 👍 and 👎 |
| `evaluate.py` | Run the test questions and measure the relevance cut-off |
| `tracing.py` | The only module that talks to Langfuse; does nothing when its keys are not set |
| `experiment.py` | The test questions as a Langfuse dataset, experiments, scores, judges, review queues |
| `judges.py` | The LLM judges: correctness in code, faithfulness, citations and relevance in Langfuse |
| `alert_drill.py` | Fire drills: made-up answers that trip each alert on purpose |

## Getting a copy

Import this into your own GitHub account rather than cloning it. You then own the
copy: you can commit to it, push to it, and keep it afterwards.

1. Sign in to GitHub and open **https://github.com/new/import**
2. **Your old repository's clone URL**:
   `https://github.com/utrains/llmops-rag-prod`
3. **Repository name**: `llmops-rag-prod`
4. Public or private, your choice
5. **Begin import**

Import copies the files and the history into a repository of your own. It is not
a fork, so nothing links back here and nothing you push comes this way.

```bash
git clone https://github.com/YOUR-USERNAME/llmops-rag-prod.git
cd llmops-rag-prod
```

## Running it

```bash
cp .env.example .env          # add your model API key
docker compose up -d --build  # indexing runs once, then the page starts
```

Then open **http://localhost:8501**. On Windows PowerShell, `Copy-Item .env.example .env` and
`start http://localhost:8501` do the first and last steps.

Indexing is a separate container that finishes before the page starts, so the app never comes
up against a half-built index.

Check what indexing produced:

```bash
docker compose exec postgres psql -U hr_app -d hr -c \
  "SELECT title, section FROM hr_policy_chunks LIMIT 5;"
```

**232 chunks from 37 documents** with the chunk size in `config.py` (700 characters, 80
overlapping). A different chunk size gives a different count. What matters is that every document
produces chunks, which is why `ingest.py` stops when one produces none, and names it.

## Tests

```bash
docker compose exec app python -m app.evaluate
```

`data/evaluation.json` holds 22 questions. Each one records the trap it is checking, so a
failure tells you which behaviour broke rather than just lowering a number:

| Trap | Questions | What it checks |
|---|---|---|
| `none` | 5 | Ordinary lookups that should simply work |
| `near-duplicate` | 3 | Two similar policies where only one is right |
| `not in corpus` | 3 | Questions the documents do not answer |
| `personal question` | 3 | Data the assistant cannot see and must not invent |
| `superseded version` | 2 | The old policy that still reads convincingly |
| `scoped exception` | 2 | A rule that applies to one region or grade only |
| `table` | 2 | Answers that live in a CSV row, not in a sentence |
| `follow-up` | 2 | A question that only makes sense after the previous one |

Run this again after any change to the prompt, the chunk size, the cut-off or the model.
These systems get worse without any error appearing.

`--thresholds` measures the relevance cut-off instead: how high the best match scores for real
questions, for small talk, and for questions the documents do not answer.

The same 22 questions also run as a Langfuse experiment, graded by these checks and by LLM
judges, so two runs can be compared side by side. See Part 3 of the lab.

## Observability with Langfuse

The lab in `docs/langfuse-lab/` adds Langfuse to the assistant one step at a time. Each part has a
guide written for someone who has never used Langfuse.

| Part | Guide | What you do |
|---|---|---|
| 1 | [Run Langfuse on your laptop](docs/langfuse-lab/01-langfuse-setup.md) | Start self-hosted Langfuse, create a project and API keys |
| 2 | [See inside every answer](docs/langfuse-lab/02-tracing.md) | Read the trace of each question: the search, the excerpts, Claude's prompt, tokens, cost, timing |
| 3 | [Grade the answers](docs/langfuse-lab/03-evaluation-scoring.md) | Datasets, experiments, LLM-as-a-judge, human review, catching a regression. Hands-on notebook. |
| 4 | [Get told when answers get worse](docs/langfuse-lab/04-alerting.md) | Monitors for faithfulness, citations, latency and cost, a local alert inbox, fire drills |

### Starting it

```powershell
cd langfuse
.\New-LangfuseEnv.ps1         # once: random secrets into langfuse\.env
docker compose up -d          # Langfuse at http://localhost:3000, alert inbox at http://localhost:8090
```

Create an account, a project and API keys at http://localhost:3000 (Part 1 shows how), put the keys
in the root `.env`, then recreate the app so it reads them:

```powershell
docker compose up -d --no-deps app
docker compose exec app python -c "from app import tracing; print(tracing.ENABLED, tracing.langfuse.auth_check())"
```

`True True` means every answer is now traced.

### What each answer records

```
hr-question        chain        the question and the conversation, the answer and its sources
├─ search-policies retriever    the excerpts kept, their similarity scores, the cut-off
└─ claude-answer   generation   the prompt Claude was given, its answer, tokens, cost
```

Chats in the page are grouped into Langfuse sessions; test runs are tagged `evaluation` and
`trap:<trap>`. The 👍 and 👎 under an answer record a `user_feedback` score on its trace.

### How answers are graded

| Score | Given by | Attached to |
|---|---|---|
| `golden_check` | the checks in `evaluate.py`, unchanged | the trace |
| `correctness` | an LLM judge comparing the answer with the test case | the trace |
| `faithfulness` | an LLM judge inside Langfuse: is every claim in the excerpts? | the `claude-answer` step |
| `citation_accuracy` | an LLM judge inside Langfuse: does each `[n]` point at the right excerpt? | the `claude-answer` step |
| `relevance` | an LLM judge inside Langfuse: does it answer the question asked? | the `claude-answer` step |
| `human_correctness` | a person, in Langfuse's Human Annotation queue | the trace |
| `user_feedback` | 👍 or 👎 in the chat page | the trace |

The judges use Claude Haiku 5.5, at a tenth of the assistant's price per token. One experiment over
the 22 questions, judges included, costs about 7 cents.

```bash
docker compose exec app python -m app.experiment setup               # score definitions and dataset
docker compose exec app python -m app.experiment run baseline --judge
docker compose exec app python -m app.experiment run tighter --judge --min-similarity 0.77
```

The second run changes the cut-off for that run only, and shows what a regression looks like:
22 of 22 passing becomes 15 of 22.

### The notebook

```powershell
docker compose up -d --no-deps notebook
docker compose logs notebook     # open the http://127.0.0.1:8888/lab?token=... link it prints
```

`notebooks/03-evaluation-scoring.ipynb` walks through Part 3 against the real assistant. It
imports the application rather than copying it.

### Alerts

Four monitors watch the scores and the answers: low faithfulness, failed citations, slow answers
and expensive answers. Langfuse sends each alert as a signed webhook to a small inbox at
http://localhost:8090, which checks the signature, refuses replays, ignores duplicates and shows
what arrived. No Slack, email or public address is involved.

Monitors and the webhook are created in Langfuse's web interface (Part 4 shows how). To check
that they fire:

```bash
docker compose exec app python -m app.alert_drill quality --watch 5
```

A drill records made-up answers in their own Langfuse environment, `alert-drill`. Nothing in the
assistant changes.

### Ports

Everything listens on localhost only.

| Address | What |
|---|---|
| http://localhost:8501 | the chat page |
| http://localhost:3000 | Langfuse |
| http://localhost:8888 | JupyterLab |
| http://localhost:8090 | the alert inbox |
| localhost:5432 | the assistant's PostgreSQL |

## Building it

`app/` holds a complete, tested version of the assistant. To build it yourself instead, start from
an empty `app/` and write the eleven modules one at a time, in the order set out in
[`docs/build-plan.md`](docs/build-plan.md). That file lists what each module has to do, and how
to check it works before you start the next one. The Langfuse modules (`tracing.py`,
`experiment.py`, `judges.py`, `alert_drill.py`) come after, with the lab.

## Settings

| Variable | What it controls |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Credentials for the model that writes the answers |
| `CLAUDE_MODEL` | The model that writes the answers, `claude-haiku-4-5` unless set |
| `MIN_BEST_SIMILARITY` | The relevance cut-off, `0.595` unless set |
| `MAX_GAP_FROM_BEST` | How far below the best match an excerpt may score and still be used, `0.10` unless set |
| `FASTEMBED_CACHE_PATH` | Where the embedding model is cached inside the container |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | The Langfuse project's API keys. Empty means no tracing. |
| `LANGFUSE_BASE_URL` | Where the app finds Langfuse: `http://host.docker.internal:3000` from inside its container |
| `JUDGE_MODEL` | The model the LLM judges use, `claude-haiku-5-5` unless set |
| `ALERT_RECEIVER_URL` | Where the fire drills read the alert inbox, `http://host.docker.internal:8090` unless set |

Every library is pinned to the version the lab was tested with, and `constraints.txt` pins the
packages they pull in, so a rebuild months from now installs the same thing. Part 3 of the lab
explains how to change a version.

The database password in `docker-compose.yml` is `local_development_only`, and it is fine
that you can read it. This stack listens on localhost and holds nothing but policy text. Do
not reuse that pattern anywhere that leaves your machine.

Langfuse's own secrets are different: `New-LangfuseEnv.ps1` generates random ones into
`langfuse/.env`, and the alert inbox's webhook secret goes in `langfuse/alert-receiver/webhook-secret`.
Git ignores both, and neither should leave your machine.
