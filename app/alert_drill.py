"""Fire drills for the Langfuse alerts: answers made on purpose to trip each monitor.

    python -m app.alert_drill quality    three wrong answers: low faithfulness, failed citations
    python -m app.alert_drill latency    one answer that takes 6 seconds
    python -m app.alert_drill cost       one answer that reports 20,000 input tokens
    python -m app.alert_drill recover    three real answers from the assistant, so quality recovers
    python -m app.alert_drill watch      print alerts as they reach the receiver

A drill records traces shaped exactly like the assistant's (hr-question, then search-policies and
claude-answer), with the excerpts real retrieval finds, in the Langfuse environment "alert-drill".
The judges grade them like any other answer and the monitors see real scores. Nothing in the
assistant changes, and only `recover` calls Claude (about half a cent).
"""
import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from app import assistant, config, retrieval, tracing

ENVIRONMENT = "alert-drill"
RECEIVER = os.environ.get("ALERT_RECEIVER_URL", "http://host.docker.internal:8090")

langfuse = tracing.langfuse

# Each wrong answer contradicts the excerpt it cites. {n} becomes the number of the excerpt that
# holds the true fact, so the citation points at a real excerpt that says something else.
WRONG_ANSWERS = [
    ("How many unused holiday days can I carry into next year, and when do they expire?", "31 March",
     "You can carry over up to 10 unused days [{n}], and they must be used by 30 June [{n}]. "
     "Any days left after that are paid out in December."),
    ("How many days of paid time off do I get each year?", "25 days",
     "Full-time employees get 30 days of paid time off a year [{n}], rising to 35 days after three "
     "years of service [{n}]."),
    ("When do I need a doctor's note if I am off sick in Berlin?", "fourth",
     "In Germany you need a doctor's note from the first day of any absence [{n}], and company sick "
     "pay stops after two weeks [{n}]."),
]

# A correct answer, for the latency and cost drills: those should trip their own monitor only
RIGHT_ANSWER = ("How many days of paid time off do I get each year?", "25 days",
                "Full-time employees get 25 days of paid time off each year [{n}].")


def now():
    return datetime.now(timezone.utc)


def drill_trace(question, fact, answer_template, *, delay=0.0, usage=None):
    """Records one assistant-shaped trace whose answer is the template, not Claude's."""
    with langfuse.start_as_current_observation(name="hr-question", as_type="chain",
                                               input={"question": question, "history": []}) as root:
        # Real retrieval, recorded as the usual search-policies step
        chunks = retrieval.search_policies(question)
        n = next((i for i, c in enumerate(chunks, start=1) if fact in c["content"]), 1)
        messages = assistant._messages(question, [], retrieval.format_excerpts(chunks))
        answer = answer_template.format(n=n)
        with langfuse.start_as_current_observation(
                name="claude-answer", as_type="generation", model=config.CLAUDE_MODEL,
                input={"system": assistant.SYSTEM_PROMPT, "messages": messages}) as generation:
            time.sleep(delay)
            # Zero usage unless a drill says otherwise: given no usage, Langfuse counts the tokens
            # in the text itself and shows a cost that was never spent
            generation.update(output=answer, usage_details=usage or {"input": 0, "output": 0},
                              metadata={"drill": True, "stop_reason": "end_turn"})
        root.update(output={"answer": answer, "route": "answered"}, metadata={"route": "answered", "drill": True})
        return root.trace_id


def run(kind):
    started = now()
    trace_ids = []
    with tracing.trace_attributes(environment=ENVIRONMENT, tags=["alert-drill", f"drill:{kind}"]):
        if kind == "quality":
            for question, fact, answer in WRONG_ANSWERS:
                trace_ids.append(drill_trace(question, fact, answer))
        elif kind == "latency":
            trace_ids.append(drill_trace(*RIGHT_ANSWER, delay=6.0))
        elif kind == "cost":
            # Langfuse prices this from the reported usage: about $0.02. No call is made, so it costs nothing.
            trace_ids.append(drill_trace(*RIGHT_ANSWER, usage={"input": 20_000, "output": 500}))
        elif kind == "recover":
            for question, _fact, _answer in WRONG_ANSWERS:
                trace_ids.append(assistant.answer(question)["trace_id"])
    tracing.flush()
    print(f"{kind} drill sent at {started:%H:%M:%S} UTC: {len(trace_ids)} traces in environment {ENVIRONMENT}")
    for trace_id in trace_ids:
        print("  trace", trace_id)
    return started, trace_ids


def receiver_alerts():
    with urllib.request.urlopen(f"{RECEIVER}/alerts.json", timeout=5) as response:
        return json.loads(response.read())["alerts"]


def watch(minutes, since=None):
    """Prints each new alert as it arrives, with how long after `since` it came."""
    since = since or now()
    known = {a["id"] for a in receiver_alerts()}
    print(f"watching {RECEIVER} for {minutes} minutes (Ctrl+C to stop)")
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        for alert in reversed(receiver_alerts()):
            if alert["id"] in known:
                continue
            known.add(alert["id"])
            received = datetime.fromisoformat(alert["received_at"])
            print(f"{received:%H:%M:%S} +{(received - since).total_seconds():4.0f}s  "
                  f"{alert['severity']:8} {alert['title']} | {alert['body']}")
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["quality", "latency", "cost", "recover", "watch"])
    parser.add_argument("--watch", type=float, default=0, metavar="MINUTES",
                        help="after the drill, watch the receiver for this many minutes")
    args = parser.parse_args()
    if not tracing.ENABLED:
        raise SystemExit("Langfuse keys are not set, so there is nowhere to send a drill.")
    if args.kind == "watch":
        watch(args.watch or 10)
        return
    started, _ = run(args.kind)
    if args.watch:
        watch(args.watch, since=started)


if __name__ == "__main__":
    main()
