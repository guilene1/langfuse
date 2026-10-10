# Build plan

`app/` ships with only `__init__.py`. The eleven modules are built in this order,
because each one needs the one before it. Run each module as you finish it: six
broken files at once is hard to debug.

The timings are a rough guide, not a target.

## Day 1: indexing

| # | File | What it does | Roughly |
|---|---|---|---|
| 1 | `config.py` | Every setting, read from environment variables | 20 min |
| 2 | `documents.py` | Read PDF, DOCX, CSV, HTML, TXT and Markdown into sections | 60 min |
| 3 | `chunking.py` | Cut sections into pieces small enough to search | 30 min |
| 4 | `embeddings.py` | Turn text into vectors | 20 min |
| 5 | `search_index.py` | Create the pgvector table, write chunks, search it | 60 min |
| 6 | `ingest.py` | Run the whole pipeline, and check the manifest first | 45 min |

**End of day 1 you can prove it works:**

```bash
docker compose run --rm ingest
docker compose exec postgres psql -U hr_app -d hr -c \
  "SELECT title, section FROM hr_policy_chunks LIMIT 5;"
```

242 chunks from 37 documents. Fewer than that means something is being dropped without an
error, which is why `ingest.py` stops when a document produces no chunks at all.

## Day 2: answering

| # | File | What it does | Roughly |
|---|---|---|---|
| 7 | `retrieval.py` | Embed the question, search, throw away weak matches | 45 min |
| 8 | `assistant.py` | Give Claude the chunks and the rules; get an answer with citations | 75 min |
| 9 | `conversations.py` | Store chats in Postgres so a refresh does not lose them | 30 min |
| 10 | `main.py` | The Streamlit page: a chat box, the answers, the sources | 40 min |
| 11 | `evaluate.py` | Run the golden set; measure the relevance threshold | 45 min |

**End of day 2:**

```bash
docker compose up -d
# open http://localhost:8501
docker compose exec app python -m app.evaluate     # 22/22
```

## While you work

- Type the code rather than pasting it. You will read it more carefully.
- Run each file as you finish it, before starting the next one.
- When something does not work, look at the data before the code. Query the
  table, print a chunk, count the rows.
