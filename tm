#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

API_URL = os.getenv("TERMINUX_API_URL", "http://127.0.0.1:8000")


def _request(method: str, path: str, **kwargs: Any) -> Any:
    url = f"{API_URL}{path}"
    with httpx.Client(timeout=20.0) as client:
        response = client.request(method=method, url=url, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{response.status_code} {response.text}")
    return response.json()


def cmd_recall(args: argparse.Namespace) -> int:
    data = _request("GET", "/v1/recall", params={"query": args.query, "limit": args.limit})
    answer = data.get("answer")
    if answer:
        print("\n=== SYNTHESIS ===")
        print(answer)
        print("=================\n")

    print(json.dumps(data, indent=2))
    return 0


def cmd_weekly_report(args: argparse.Namespace) -> int:
    data = _request("GET", "/v1/weekly-report", params={"days": args.days})
    print(json.dumps(data, indent=2))
    return 0


def cmd_replay_session(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {}
    if args.session_id is not None:
        params["session_id"] = args.session_id
    if args.query is not None:
        params["query"] = args.query
    data = _request("GET", "/v1/replay-session", params=params)
    print(json.dumps(data, indent=2))
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    payload = {"task": args.task, "commands": args.commands or []}
    data = _request("POST", "/v1/preflight", json=payload)
    warnings = data.get("warnings", [])

    if not warnings:
        print("\033[32m✓ No historical warnings found.\033[0m")
        return 0

    severity_colors = {"high": "\033[31m", "medium": "\033[33m", "low": "\033[36m"}
    reset = "\033[0m"
    bold = "\033[1m"

    print(f"\n{bold}=== PREFLIGHT WARNINGS ==={reset}")
    for w in warnings:
        sev = w.get("severity", "medium")
        color = severity_colors.get(sev, "\033[33m")
        label = sev.upper()
        print(f"  {color}[{label}]{reset} {w['message']}")
        event_ids = w.get("evidence_event_ids", [])
        if event_ids:
            print(f"         evidence: event_ids={event_ids}")
    print(f"{bold}========================={reset}\n")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    payload = {
        "command": args.command,
        "cwd": args.cwd,
        "output": args.output,
        "exit_code": args.exit_code,
        "duration_ms": args.duration_ms,
    }
    data = _request("POST", "/v1/events", json=payload)
    print(json.dumps(data, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tm", description="Terminux CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    recall = subparsers.add_parser("recall", help="Recall historical issues and fixes")
    recall.add_argument("query", help="Issue/topic to query")
    recall.add_argument("--limit", type=int, default=5)
    recall.set_defaults(func=cmd_recall)

    weekly = subparsers.add_parser("weekly-report", help="Generate weekly operational report")
    weekly.add_argument("--days", type=int, default=7)
    weekly.set_defaults(func=cmd_weekly_report)

    replay = subparsers.add_parser("replay-session", help="Replay a captured terminal session")
    replay.add_argument("--session-id", type=int)
    replay.add_argument("--query")
    replay.set_defaults(func=cmd_replay_session)

    preflight = subparsers.add_parser("preflight", help="Warn about historically failing chains")
    preflight.add_argument("task", help="Task description")
    preflight.add_argument("commands", nargs="*", help="Optional command chain")
    preflight.set_defaults(func=cmd_preflight)

    ingest = subparsers.add_parser("ingest", help="Send a sample event to Terminux API")
    ingest.add_argument("command")
    ingest.add_argument("--cwd", default=os.getcwd())
    ingest.add_argument("--output", default="")
    ingest.add_argument("--exit-code", type=int, default=0)
    ingest.add_argument("--duration-ms", type=int)
    ingest.set_defaults(func=cmd_ingest)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
