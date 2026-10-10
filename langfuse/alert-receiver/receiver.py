"""A local inbox for Langfuse alerts: receives signed webhooks and shows them on a web page.

Langfuse posts each alert to http://alert-receiver/langfuse (inside Docker). Open
http://localhost:8090 to see them. Python's standard library only, so nothing to install.

Every request is checked before it is stored:

- Signature. Langfuse signs each delivery with the automation's secret: the header
  `x-langfuse-signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">`. The secret is read
  from webhook-secret next to this file on every request, so pasting it in needs no restart.
  Until it is there, every delivery is rejected.
- Age. A delivery signed more than five minutes ago is rejected, so a recorded request cannot
  be replayed later.
- Duplicates. Langfuse retries a delivery that failed with the same envelope id; one already
  stored is acknowledged but not stored again.

Alerts are kept in /data/alerts.jsonl, so they survive a restart.
"""
import hashlib
import hmac
import html
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SECRET_FILE = Path(os.environ.get("SECRET_FILE", "/srv/webhook-secret"))
STORE = Path(os.environ.get("STORE", "/data/alerts.jsonl"))
MAX_AGE_SECONDS = 300
MAX_BODY_BYTES = 1_000_000

lock = threading.Lock()
alerts = []            # newest last
seen_ids = set()
counters = {"accepted": 0, "duplicates": 0, "rejected": 0}
rejections = []        # the last few reasons, shown on the page to help with setup


def log(message):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z {message}", flush=True)


def load_store():
    if STORE.exists():
        for line in STORE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                alerts.append(record)
                seen_ids.add(record["id"])
    log(f"receiver ready, {len(alerts)} stored alerts")


def read_secret():
    try:
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def check_signature(header, body, secret):
    """Returns None if the signature is valid, otherwise the reason it is not."""
    if not secret:
        return "no secret configured: save the automation's secret in langfuse/alert-receiver/webhook-secret"
    parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    if "t" not in parts or "v1" not in parts:
        return "missing or malformed x-langfuse-signature header"
    try:
        signed_at = int(parts["t"])
    except ValueError:
        return "signature timestamp is not a number"
    if abs(time.time() - signed_at) > MAX_AGE_SECONDS:
        return f"signature is {int(time.time() - signed_at)}s old, more than {MAX_AGE_SECONDS}s: possible replay"
    expected = hmac.new(secret.encode(), f"{signed_at}.".encode() + body, hashlib.sha256).hexdigest()
    # compare_digest takes the same time however many characters match, so timing reveals nothing
    if not hmac.compare_digest(expected, parts["v1"]):
        return "signature does not match: wrong secret, or the body was changed"
    return None


def reject(reason):
    with lock:
        counters["rejected"] += 1
        rejections.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason})
        del rejections[:-5]
    log(f"REJECTED: {reason}")


def summarise(envelope):
    # Only these fields are kept. A monitor alert carries no questions or answers, only the
    # monitor's name, severity, time window and links back to Langfuse.
    alert = envelope.get("payload") or {}
    message = alert.get("message") or {}
    return {
        "id": envelope["id"],
        "type": envelope.get("type"),
        "sent_at": envelope.get("timestamp"),
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "severity": alert.get("severity"),
        "title": message.get("title"),
        "body": message.get("body"),
        "window": alert.get("window"),
        "from": alert.get("fromTimestamp"),
        "to": alert.get("toTimestamp"),
        "monitor_id": alert.get("monitorId"),
        "permalink": alert.get("permalink"),
        "data_permalink": alert.get("dataPermalink"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "alert-receiver"

    def log_message(self, *args):
        pass  # one line per alert is logged instead of one per request

    def send(self, status, content_type, text):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/langfuse":
            return self.send(404, "application/json", '{"error": "post alerts to /langfuse"}')
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY_BYTES:
            reject("body too large")
            return self.send(413, "application/json", '{"error": "too large"}')
        body = self.rfile.read(length)
        problem = check_signature(self.headers.get("x-langfuse-signature", ""), body, read_secret())
        if problem:
            reject(problem)
            return self.send(401, "application/json", json.dumps({"error": problem}))
        try:
            envelope = json.loads(body)
            envelope_id = envelope["id"]
        except (ValueError, KeyError, TypeError):
            reject("signed, but the body is not a Langfuse webhook envelope")
            return self.send(400, "application/json", '{"error": "not a webhook envelope"}')
        with lock:
            if envelope_id in seen_ids:
                counters["duplicates"] += 1
                duplicate = True
            else:
                record = summarise(envelope)
                alerts.append(record)
                seen_ids.add(envelope_id)
                STORE.parent.mkdir(parents=True, exist_ok=True)
                with STORE.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
                counters["accepted"] += 1
                duplicate = False
        if duplicate:
            log(f"duplicate delivery ignored: {envelope_id}")
        else:
            log(f"ALERT {record['severity']}: {record['title']} | {record['body']}")
        # 200 either way: a duplicate means Langfuse is retrying, and it should stop
        self.send(200, "application/json", '{"ok": true}')

    def do_GET(self):
        if self.path == "/alerts.json":
            with lock:
                data = {"alerts": list(reversed(alerts)), "counters": dict(counters),
                        "rejections": list(reversed(rejections)), "secret_configured": bool(read_secret())}
            return self.send(200, "application/json", json.dumps(data))
        if self.path in ("/", "/index.html"):
            return self.send(200, "text/html; charset=utf-8", page())
        self.send(404, "text/plain", "not found")


COLOURS = {"ALERT": "#c62828", "WARNING": "#ef6c00", "OK": "#2e7d32", "NO_DATA": "#616161"}


def plain(text):
    # The alert body is Markdown; show it as text, escaped
    return html.escape(re.sub(r"[*`]", "", text or ""))


def page():
    with lock:
        rows = list(reversed(alerts))
        stats = dict(counters)
        recent_rejections = list(reversed(rejections))
    secret = "configured" if read_secret() else "<b style='color:#c62828'>not configured: every delivery is rejected</b>"
    items = []
    for a in rows:
        colour = COLOURS.get(a.get("severity"), "#455a64")
        link = (f"<a href='{html.escape(a['permalink'])}' target='_blank' rel='noopener'>open in Langfuse</a>"
                if a.get("permalink") else "")
        items.append(f"""
<div class="alert" style="border-left-color:{colour}">
  <div><span class="badge" style="background:{colour}">{html.escape(str(a.get('severity')))}</span>
  <b>{plain(a.get('title'))}</b></div>
  <div>{plain(a.get('body'))}</div>
  <div class="meta">received {html.escape(a['received_at'])} · window {html.escape(str(a.get('window')))}
  ({html.escape(str(a.get('from')))} to {html.escape(str(a.get('to')))}) · signature verified · {link}</div>
</div>""")
    rejected = "".join(f"<li>{html.escape(r['at'])}: {html.escape(r['reason'])}</li>" for r in recent_rejections)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5"><title>Alert inbox</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 0 auto; max-width: 860px; padding: 16px; color: #212121; background: #fafafa; }}
 .alert {{ background: #fff; border-left: 6px solid; padding: 10px 14px; margin: 10px 0; border-radius: 4px; box-shadow: 0 1px 2px #0002; }}
 .badge {{ color: #fff; padding: 1px 8px; border-radius: 10px; font-size: 12px; margin-right: 6px; }}
 .meta {{ color: #616161; font-size: 13px; margin-top: 4px; }}
 .stats {{ color: #424242; font-size: 14px; }}
</style></head><body>
<h1>Alert inbox</h1>
<p class="stats">Webhook secret: {secret} · accepted {stats['accepted']} · duplicates ignored {stats['duplicates']}
· rejected {stats['rejected']} · refreshes every 5 seconds</p>
{''.join(items) or '<p>No alerts yet.</p>'}
{f'<h3>Recently rejected</h3><ul>{rejected}</ul>' if rejected else ''}
</body></html>"""


if __name__ == "__main__":
    load_store()
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
