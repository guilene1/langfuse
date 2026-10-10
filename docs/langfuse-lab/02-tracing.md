# Part 2: See inside every answer with traces

In Part 1 you started Langfuse and created API keys. In this part the assistant sends Langfuse a
**trace** for every question, and you use those traces to see why an answer came out the way it
did.

**Time:** about 30 minutes.

## Words you will see

| Word | Meaning here |
|---|---|
| **Trace** | Everything that happened for one question, from the moment it was asked to the answer |
| **Observation** | One step inside a trace. A trace is a tree of observations. |
| **Span** | An observation that is ordinary code, such as searching the database |
| **Generation** | An observation that is a call to a language model. Langfuse records its prompt, answer, tokens and cost. |
| **Session** | Several traces that belong to one conversation |
| **Tag** | A label on a trace, used to filter, such as `evaluation` |
| **Metadata** | Extra facts attached to an observation, such as the similarity score |

## What one trace looks like

```
hr-question        chain        the whole question, from input to final answer
├─ search-policies retriever    embed the question, search pgvector, drop weak matches
└─ claude-answer   generation   Claude writes the answer from the excerpts
```

| Observation | Input | Output | Metadata |
|---|---|---|---|
| `hr-question` | the question and the conversation so far | the answer, its sources, and `trace_id` | `route`: which path produced the answer |
| `search-policies` | the text that was searched (a follow-up includes the earlier topic) | the excerpts that were kept, with their similarity scores | `best_similarity`, `cutoff`, `kept`, `candidates`, `keyword_fallback` |
| `claude-answer` | the system prompt and the messages sent to Claude | the answer text | `stop_reason`, `request_id`. Also the model, token usage and cost. |

`route` tells you which of three paths the assistant took:

| `route` | What happened | Steps in the trace |
|---|---|---|
| `answered` | Excerpts were found and Claude wrote an answer | search and Claude |
| `no_policy` | The search kept nothing, so a fixed "I could not find anything" reply was sent | search only |
| `small_talk` | The message was a greeting or thanks, so nothing was searched | none |

You never see costs for `no_policy` and `small_talk`, because Claude is not called.

## How the code does it

All of the tracing lives in [`app/tracing.py`](../../app/tracing.py). The rest of the code only
marks which functions are steps:

```python
from app.tracing import observe

@observe(name="search-policies", as_type="retriever")
def search_policies(question):
    ...
```

`@observe` records the function's arguments as the input and its return value as the output,
and times it. A function called from inside another observed function becomes its child, which
is how the tree is built.

Two things are added by hand:

- **Token usage**, in `assistant._generate`. Claude reports how many tokens it read and wrote,
  and the code passes those numbers on. Langfuse then works out the cost from its own price
  table. No price is written in the code.
- **Sessions and tags**, in `main.py` and `evaluate.py`. Each chat in the page is one session
  named `chat-<number>`. Each evaluation case is a session named `eval-<run>-<case>`, with the
  tags `evaluation` and `trap:<trap>`.

## Tracing is optional

The assistant works the same whether Langfuse is there or not:

| Situation | What happens |
|---|---|
| `LANGFUSE_PUBLIC_KEY` or `LANGFUSE_SECRET_KEY` is empty | Tracing is off. Nothing is sent, and nothing is logged as an error. |
| Keys set, Langfuse stopped | Answers still work. The traces are lost, and the app's log shows export errors. |
| Keys set, Langfuse running | Traces are sent in the background, in batches every few seconds |

To switch tracing off, empty the two keys in the root `.env`, then recreate the app container
(Step 1 shows how).

## Step 1: Load the keys into the app

The app reads `.env` only when its container is created. After you change the keys:

```powershell
docker compose up -d --no-deps app
```

`--no-deps` recreates only the app. The database and its documents are not touched.

Check that the app can reach Langfuse:

```powershell
docker compose exec app python -c "from app import tracing; print('tracing on:', tracing.ENABLED, '| keys accepted:', tracing.langfuse.auth_check())"
```

You want `tracing on: True | keys accepted: True`.

## Step 2: Make some traces

Open **http://localhost:8501** and ask:

1. `How many unused holiday days can I carry into next year?`
2. `what about in Berlin` (a follow-up with no topic of its own)
3. `Does Northwind offer pet insurance?`
4. `hello`

Or run the test questions, which makes about 26 traces in under a minute, for about 5 cents of
Claude usage:

```powershell
docker compose exec app python -m app.evaluate
```

## Step 3: Read the traces

Open **http://localhost:3000**, then your project. Open the traces list from the left sidebar.

Click a trace for question 1 and look at:

- **The tree.** `hr-question` with `search-policies` and `claude-answer` under it.
- **`search-policies`, Output.** Every excerpt says which document and version it came from.
  The 2024 Time Off Policy, which says 10 days, must not be there. Only current documents are
  searched.
- **`claude-answer`, Input.** This is exactly what Claude was given: the rules, then the
  numbered excerpts. If an answer is wrong, check whether the fact was in here.
- **`claude-answer`, usage and cost.** About 1,800 tokens in and 80 out, at about $0.002.
- **Timings.** The search takes hundredths of a second and Claude takes about 1.5 seconds. If
  the assistant feels slow, this tells you where to look.

### Question 2: a follow-up

Open the **search-policies** observation. Its input is not `what about in Berlin` but the
earlier question with the follow-up added. Searched on its own, "Berlin" matches the travel
policy. Both questions sit in the same **session**, so you can read the conversation in
order.

### Question 3: a question the documents do not answer

Nothing in the documents mentions pet insurance, yet its `best_similarity` is about 0.76,
higher than some real questions. No threshold can tell them apart, so the excerpts reach
Claude, and only the system prompt's rule ("if the excerpts do not answer the question, say so")
stops it inventing an answer. The trace shows both halves of that: the excerpts that were
sent, and an answer that cites none of them.

### Question 4: small talk

A trace with no children and `route: small_talk`. Nothing was searched, Claude was not called,
and it cost nothing.

## Step 4: Filter

Try these filters on the traces list:

- **Tag `evaluation`**: only the test runs
- **Tag `trap:superseded version`**: the two questions with an outdated policy in the corpus
- **Sort by latency**: the slowest answers first

## If something does not show up

| Symptom | Cause and fix |
|---|---|
| No traces at all | Run the check in Step 1. `tracing on: False` means the keys did not reach the container: recreate it. `keys accepted: False` means the keys do not belong to this Langfuse. |
| Traces appear a few seconds late | Normal. They are sent in batches, and the worker takes a moment to process them. |
| Costs show `$0` | Langfuse has no price for the model. See "Costs show $0" in [Part 1](01-langfuse-setup.md#troubleshooting). |
| `LANGFUSE_BASE_URL` points to `localhost` | From inside the container that means the container itself. Use `http://host.docker.internal:3000`. |

## For the next part

Every answer now carries a `trace_id`. Part 3 uses it to attach scores to traces: whether
the test question passed, and what a user thought of the answer.
