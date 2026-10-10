# Northwind HR Policy Assistant

An internal assistant that answers employees' HR questions from Northwind Systems' own policy
documents. Every answer carries a citation, and when the documents do not cover the question,
the assistant says so instead of guessing.

It runs on a laptop: PostgreSQL with pgvector holds the text, a Streamlit page handles the
chat, and a language model writes the answers. Two containers, one `docker compose up`.

```
Q: How many days of paid time off do I get each year?
A: 25 days a year for full-time employees, plus public holidays.
   Sources: Time Off Policy (NW-POL-002, v2026.1)

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

## What is in the repository

```
data/documents/       37 HR policy documents in six formats, and manifest.json
data/evaluation.json  22 test questions, each with the trap it is checking for
app/                  the application
Dockerfile            the image the app runs in
docker-compose.yml    PostgreSQL with pgvector, plus the app
requirements.txt      the libraries
.env.example          copy to .env and add your model API key
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
| `main.py` | The Streamlit page: a chat box, the answers, the sources |
| `evaluate.py` | Run the test questions and measure the relevance cut-off |

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
open http://localhost:8501
```

Indexing is a separate container that finishes before the page starts, so the app never comes
up against a half-built index.

Check what indexing produced:

```bash
docker compose exec postgres psql -U hr_app -d hr -c \
  "SELECT title, section FROM hr_policy_chunks LIMIT 5;"
```

**242 chunks from 37 documents.** Fewer than that means something is being dropped without an
error, which is why `ingest.py` stops when a document produces no chunks at all.

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

## Building it

`app/` contains only `__init__.py`. The eleven modules are written one at a time, in the
order set out in [`docs/build-plan.md`](docs/build-plan.md). That file lists what each module
has to do, and how to check it works before you start the next one.

## Settings

| Variable | What it controls |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Credentials for the model that writes the answers |
| `FASTEMBED_CACHE_PATH` | Where the embedding model is cached inside the container |

The database password in `docker-compose.yml` is `local_development_only`, and it is fine
that you can read it. This stack listens on localhost and holds nothing but policy text. Do
not reuse that pattern anywhere that leaves your machine.
