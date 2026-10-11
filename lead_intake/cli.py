"""Command line.

    python -m lead_intake.cli serve                 # API and review page on http://127.0.0.1:8024
    python -m lead_intake.cli demo                  # run 24 sample emails through the pipeline and print where each went
    python -m lead_intake.cli process email.json    # one email (JSON with from_email, subject, body), prints the decision
    python -m lead_intake.cli send URL --count 10   # post sample emails to a webhook, e.g. the n8n workflow
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import analyze
from .config import DATA, Settings
from .llm import make_provider
from .schema import EmailIn

PORT = 8024


def samples(count: int) -> list[dict]:
    """A mix of every kind of email, in file order within a kind."""
    rows = [json.loads(line) for line in (DATA / "emails.jsonl").read_text().splitlines()]
    kinds: dict[str, list[dict]] = {}
    for row in rows:
        kinds.setdefault(row["kind"], []).append(row)
    picked: list[dict] = []
    while len(picked) < count and any(kinds.values()):
        for queue in kinds.values():
            if queue and len(picked) < count:
                picked.append(queue.pop(0))
    return picked


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("lead_intake.api:create_app", factory=True, host=args.host, port=args.port, log_level="info")


def cmd_demo(args: argparse.Namespace) -> None:
    from . import pipeline
    from .store import Store

    settings = Settings.from_env()
    if args.fresh:
        Path(settings.db_path).unlink(missing_ok=True)
    provider, store = make_provider(settings), Store(settings.db_path)
    print(f"provider: {provider.name}   database: {settings.db_path}\n")
    print(f"{'id':5} {'kind':28} {'category':16} {'route':8} {'score':>5}  reason")
    for row in samples(args.count):
        out = pipeline.intake(EmailIn(**row["email"]), provider, store, settings)
        a = out.get("analysis")
        score = f"{a['score']:>5}" if a and a["category"] == "lead" else "    -"
        reason = "already processed" if out["duplicate"] else out["route_reason"]
        print(f"{row['id']:5} {row['kind']:28} {out['category']:16} {out['route']:8} {score}  {reason}")
    print("\n" + json.dumps(store.stats()))
    print(f"\nOpen the review queue and the CRM table: python -m lead_intake.cli serve, then http://127.0.0.1:{PORT}")


def cmd_process(args: argparse.Namespace) -> None:
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
    settings = Settings.from_env()
    a = analyze(EmailIn(**json.loads(raw)), make_provider(settings), settings)
    print(a.model_dump_json(indent=2, exclude={"email"}))


def cmd_send(args: argparse.Namespace) -> None:
    import httpx

    headers = {"X-API-Key": args.token} if args.token else {}
    for row in samples(args.count):
        response = httpx.post(args.url, json=row["email"], headers=headers, timeout=300)
        try:
            body = response.json()
            text = f"{body.get('category')} -> {body.get('route')} ({body.get('route_reason')})"
        except ValueError:
            text = response.text[:120]
        print(f"{row['id']} {row['kind']:28} HTTP {response.status_code}  {text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lead intake: inbound email to CRM")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the API and the review page")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=PORT)
    serve.set_defaults(run=cmd_serve)
    demo = sub.add_parser("demo", help="run sample emails through the pipeline")
    demo.add_argument("--count", type=int, default=24)
    demo.add_argument("--fresh", action="store_true", help="delete the database first")
    demo.set_defaults(run=cmd_demo)
    process = sub.add_parser("process", help="analyse one email from a JSON file ('-' for stdin); nothing is stored")
    process.add_argument("file")
    process.set_defaults(run=cmd_process)
    send = sub.add_parser("send", help="post sample emails to a webhook URL")
    send.add_argument("url")
    send.add_argument("--count", type=int, default=10)
    send.add_argument("--token", help="value for the X-API-Key header, if the target wants one")
    send.set_defaults(run=cmd_send)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
