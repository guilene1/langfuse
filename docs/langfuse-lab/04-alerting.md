# Part 4: Get told when the answers get worse

Parts 2 and 3 let you look at traces and scores. Nobody looks all the time. In this part Langfuse
watches the scores, the latency and the cost for you, and sends an alert when one of them goes wrong.

**Time:** about 45 minutes, a third of it waiting for alerts to arrive.
**Claude cost:** under 2 cents.

You will:

1. Start a small local **alert inbox** that receives Langfuse's alerts (no Slack, email or internet).
2. Connect Langfuse to it with a signed **webhook**.
3. Create four **monitors**: low faithfulness, failed citations, slow answers, expensive answers.
4. Run **fire drills** that trip each one on purpose, and watch the alerts arrive.

## Words you will see

| Word | Meaning here |
|---|---|
| **Monitor** | A rule Langfuse checks every minute: "the average faithfulness over the last 5 minutes is below 0.8". Langfuse's menu calls these **Alerts**. |
| **Window** | How far back a monitor looks each time it checks: 5 minutes at least, up to a week |
| **Threshold** | The line that, once crossed, raises an alert. A monitor can also have a lower **warning** line. |
| **Severity** | A monitor's state: OK, WARNING, ALERT, or NO_DATA when nothing happened in the window |
| **Automation** | Where an alert is sent: here a **webhook**, an HTTP request to our inbox |
| **Webhook secret** | A key shared by Langfuse and the inbox. Langfuse signs every alert with it, so the inbox can tell a real alert from a fake one. |
| **Fire drill** | Made-up data, sent on purpose, to check that an alert really fires |

## How it fits together

```
 Northwind app ──traces──▶ Langfuse ──judges score answers──▶ scores
                              │
                              │ every minute: monitors check scores, latency, cost
                              ▼
                       severity changes ──signed webhook──▶ alert-receiver ──▶ http://localhost:8090
                                         http://alert-receiver/langfuse        (your alert inbox)
```

The inbox runs as a container next to Langfuse. Langfuse reaches it at `http://alert-receiver/langfuse`
inside Docker; you open it at **http://localhost:8090**.

## What Langfuse can alert on

| It can watch | With | Filtered by |
|---|---|---|
| Observations: latency (milliseconds), cost (USD), tokens, count | avg, sum, count, min, max, p50 to p99 | observation name and type, environment, tags, model, session, experiment |
| Numeric scores, such as faithfulness | the same | score name, environment, the judge that gave it, the observation it scored |
| True/false scores, such as citation_accuracy | the same; the average is the share that are true | the same, plus true or false |

What it **cannot** do on its own:

- Wait for enough data: one bad score in a quiet window is enough to alert.
- Combine conditions ("faithfulness low **and** more than 10 answers").
- Notice that something is unusual compared with last week.
- Put the measured value, or the questions behind it, in the alert. The alert says "below 0.8"; the
  link in it takes you to the data.
- Be created from code: monitors and automations are made in the web interface.

Comparing experiment runs is not an alert's job either: Part 3's pass-rate gate does that.

## Step 1: Start the alert inbox

From the `langfuse` folder:

```powershell
docker compose up -d alert-receiver
```

Open **http://localhost:8090**. It says *No alerts yet* and *Webhook secret: not configured*. Until
the secret is there, the inbox **rejects every delivery**: an alert nobody signed is not trusted.

> The Langfuse containers also need to know they may send webhooks to the inbox. Langfuse refuses
> webhook addresses inside private networks, so a webhook cannot be used to reach internal systems.
> `docker-compose.override.yml` lets exactly one host through, `alert-receiver`. If you installed
> Langfuse before this part was added, apply it once:
> `docker compose up -d --no-deps langfuse-web langfuse-worker`

## Step 2: Create the webhook automation

*The menu names below come from Langfuse 4.56.0's source code.*

1. In Langfuse, open **Alerts** in the left sidebar.
2. Start a new monitor. In its **Automations** section choose **New automation**, then **Webhook**.
3. URL: `http://alert-receiver/langfuse`. Leave the headers as they are.
4. Save. Langfuse shows the **Webhook Secret**, starting `lf-whsec_`, **only this once**. Copy it.
5. Save it in a file named `webhook-secret` in `langfuse\alert-receiver\`, nothing else in the file:

   ```powershell
   Set-Content -NoNewline -Encoding ascii langfuse\alert-receiver\webhook-secret "lf-whsec_paste-yours-here"
   ```

   Git ignores this file. The inbox reads it on every request, so no restart is needed. Refresh
   http://localhost:8090: *Webhook secret: configured*.

Lost the secret? Open the automation in Langfuse and regenerate it, then save the new one in the file.

## Step 3: Create the four monitors

All four use the webhook automation from Step 2 and these settings:

- **Renotify:** *Off (alert only on transitions)*. You hear once when a monitor goes bad and once when it
  recovers, not every minute in between.
- **Environment filter:** `default` and `alert-drill`. `default` is the real chat traffic; `alert-drill`
  is where the fire drills write. Leave out `sdk-experiment`, so running an experiment never raises an
  alert. In real use you would also remove `alert-drill`.

| Monitor | View, measure, aggregation | Other filters | Alert when | Warning when | When there is no data |
|---|---|---|---|---|---|
| **Low faithfulness** | scores-numeric, value, **avg** | name = `faithfulness` | below 0.8 | below 0.9 | **show no data** |
| **Citation failures** | scores-boolean, **count** | name = `citation_accuracy`, value = false | above 0 | | treat as zero |
| **Slow answers** | observations, latency, **p95** | name = `claude-answer`, type = GENERATION | above 5000 | above 3000 | show no data |
| **Expensive answer** | observations, totalCost, **max** | name = `claude-answer`, type = GENERATION | above 0.01 | | show no data |

Window: **5m** for all four, the shortest there is, so the drills show results quickly.

### Watch out for "When there is no data"

Langfuse's default is to **treat no data as zero**. For "average faithfulness below 0.8", zero is below
0.8, so the monitor would raise an alert every time nobody used the assistant for five minutes. Choose
*show no data* for faithfulness, latency and cost.

For citation failures zero is the right answer: no failed citations in the window means all is well.

### Why these numbers

- Answers normally take about 1.6 seconds; the slowest seen while building the lab took 4 seconds.
  5 seconds is clearly abnormal.
- An answer normally costs about $0.002. A cent means something changed: a much longer prompt, more
  excerpts, or a more expensive model.
- Faithfulness was 0.90 on average in Part 3's experiments, so 0.8 leaves room for the judge's
  occasional strictness.

## Step 4: Run the fire drills

A fire drill records answers that look exactly like the assistant's, with the excerpts real retrieval
finds, in the environment `alert-drill`. The judges grade them like any other answer. Nothing in the
assistant changes.

| Drill | What it sends | Should trip | Claude cost |
|---|---|---|---|
| `quality` | three answers that contradict the excerpts they cite | Low faithfulness, Citation failures | about $0.003 (the judges) |
| `latency` | one correct answer that takes 6 seconds | Slow answers | about $0.001 |
| `cost` | one correct answer that reports 20,000 input tokens; Langfuse prices it at $0.0225, but nothing is spent | Expensive answer | about $0.001 |
| `recover` | three real answers from the assistant | the quality monitors recover | about $0.006 |

From the repository folder, run a drill and watch the inbox from the same window:

```powershell
docker compose exec app python -m app.alert_drill quality --watch 5
```

`--watch 5` prints each alert that arrives in the next five minutes, and how long after the drill it
came. Keep http://localhost:8090 open as well.

### How long it takes

Expected, from Langfuse's source code (to be replaced with measured times):

| Step | Time |
|---|---|
| The judges score the answers | about 10 seconds |
| Langfuse waits for the data to settle | 30 seconds |
| The next check: every minute, at a moment that differs per monitor | up to 2 minutes |
| **Drill to inbox** | **about 1 to 3 minutes** |

Recovery takes longer: the bad scores have to leave the 5-minute window first.

### Then try

1. **Run `quality` again** while the monitor is still in ALERT. Nothing arrives: with renotify off, a
   monitor reports when its state changes, not while it stays bad.
2. **Run `latency` and `cost`.** Each trips only its own monitor, because the answer is correct.
3. **Wait five minutes, run `recover`.** The quality monitors go back to OK, and a *recovered* alert arrives.
4. Open an alert's link: it goes to the monitor in Langfuse, with the chart of what crossed the line.

## Keeping alerts quiet and judges cheap

| Problem | What prevents it |
|---|---|
| The same alert every minute | Renotify off: one alert when the state changes, one when it recovers |
| Alerts when nobody uses the app | "Show no data" on faithfulness, latency and cost |
| Every small dip pages someone | A warning line below the alert line |
| Langfuse retries a delivery, the inbox gets it twice | The inbox ignores an envelope id it has already stored |
| Experiments raise alerts | The monitors leave out the `sdk-experiment` environment |
| Judges cost too much on busy traffic | Sampling: see below |

### Judge sampling after the lab

During the lab every answer is judged. Each judgement costs about $0.00027 and there are three per
answer, so judging adds about 40% to the cost of each live answer. Afterwards, judge one live answer in
five, but keep every experiment and drill:

```powershell
docker compose exec app python -m app.experiment judges --live-sampling 0.2
```

This replaces the single rule with three, all visible in **Evaluators**:

| Rule | Environment | Judged |
|---|---|---|
| Judge live answers | default | 20% |
| Judge experiments | sdk-experiment | all: a sampled experiment could not be compared with the last one |
| Judge alert drills | alert-drill | all: a drill must trip its alert every time |

Judging then adds about 8% to the cost of a live answer. With fewer live scores, give the quality
monitors a longer window in real use, for example **1h**, so one unlucky answer does not decide.

## Security

- **Every alert is signed.** Langfuse sends `x-langfuse-signature: t=<time>,v1=<HMAC-SHA256>`, computed
  over the time and the body with the webhook secret. The inbox recomputes it and rejects anything
  that does not match, or was changed on the way.
- **Old signatures are refused.** A delivery signed more than 5 minutes ago is rejected, so a captured
  alert cannot be replayed later.
- **The secret stays local:** in a git-ignored file, mounted read-only, never printed or logged.
- **Only one private host is allowed.** The allowlist names `alert-receiver` alone, not a network range.
- **Nothing is public.** The inbox listens on localhost and inside Docker; no relay service is used.
- **Alerts contain no questions or answers.** They carry the monitor's name, its state, the time window,
  the filters, and links into Langfuse (which need a login). Keep monitor names and filters free of
  anything sensitive, because they are sent.
- **Drill data is made up**, and kept in its own environment.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Langfuse refuses the webhook URL | The allowlist is missing: `docker compose up -d --no-deps langfuse-web langfuse-worker` from the `langfuse` folder |
| The inbox lists *Recently rejected: no secret configured* | Save the secret, Step 2 |
| *signature does not match* | The file holds the wrong secret, or extra characters. Save it again with the command in Step 2. |
| *signature is ...s old* | The computer's clock is off by more than 5 minutes. Let Windows sync the time. |
| No alert after 5 minutes | Look at the monitor in **Alerts**: is it in ALERT? If yes, it is the delivery: check the inbox's rejected list and `docker compose logs alert-receiver`. If no, check its filters and that the drill's scores exist (**Scores**, environment `alert-drill`). |
| The automation turned itself off | Langfuse disables an automation after 5 failed deliveries in a row, for example while the inbox was stopped. Start the inbox, then switch the automation back on in Langfuse. |
| An alert every time nobody chats | "When there is no data" is set to treat no data as zero. Use *show no data*. |
| `faithfulness` sometimes has no reasoning | It happens occasionally; the score itself is still there. |

## Commands

```powershell
# from the langfuse folder
docker compose up -d alert-receiver          # start the inbox
docker compose logs -f alert-receiver        # one line per alert or rejection
docker compose stop alert-receiver           # stop it (Langfuse disables the automation after 5 failed sends)

# from the repository folder
docker compose exec app python -m app.alert_drill quality --watch 5
docker compose exec app python -m app.alert_drill latency --watch 5
docker compose exec app python -m app.alert_drill cost --watch 5
docker compose exec app python -m app.alert_drill recover --watch 8
docker compose exec app python -m app.alert_drill watch --watch 10      # just watch
```
