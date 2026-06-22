#!/home/falloficaruss/miniforge3/envs/dev/bin/python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any

import subprocess
import textwrap

import httpx
import time
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

API_URL = os.getenv("TERMINUX_API_URL", "http://127.0.0.1:8000")
console = Console()
err_console = Console(stderr=True)


def _request(method: str, path: str, **kwargs: Any) -> Any:
    url = f"{API_URL}{path}"
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
            ) as client:
                response = client.request(method=method, url=url, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError(f"{response.status_code} {response.text}")
            return response.json()
        except httpx.ConnectError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1 * (2**attempt))
    raise RuntimeError(
        f"Failed to connect to Terminux API at {API_URL}. "
        f"Is the backend running? (last error: {last_exc})"
    )


def _format_ts(iso: str | None) -> str:
    """Format ISO timestamp into a compact, readable form."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(iso)[:16]


def _truncate(text: str, maxlen: int = 60) -> str:
    """Truncate text with ellipsis if too long."""
    text = text.replace("\n", " ").strip()
    if len(text) <= maxlen:
        return text
    return text[: maxlen - 1] + "…"


def _score_style(score: float) -> str:
    """Return a Rich colour tag based on similarity score."""
    if score >= 0.75:
        return "bold green"
    elif score >= 0.5:
        return "yellow"
    elif score >= 0.25:
        return "dim yellow"
    return "dim"


def _exit_code_style(code: int) -> str:
    return "green" if code == 0 else "bold red"


_INLINE_MD = re.compile(r'`([^`]+)`|\*\*([^*]+)\*\*')


def _render_inline(text: str) -> Text:
    result = Text()
    last = 0
    for m in _INLINE_MD.finditer(text):
        result.append(text[last:m.start()])
        if m.group(1):
            result.append(m.group(1), style="bold cyan")
        else:
            result.append(m.group(2), style="bold")
        last = m.end()
    result.append(text[last:])
    return result


def _detect_project_root() -> str | None:
    """Detect project root from current directory."""
    import os
    cwd = os.getcwd()
    markers = [".git", ".hg", "package.json", "pyproject.toml", "go.mod", "Cargo.toml"]
    path = cwd
    while path:
        for marker in markers:
            if os.path.exists(os.path.join(path, marker)):
                return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return None


def _extract_suggested_command(answer: str) -> str | None:
    """Extract a suggested bash command from '### Suggested Action' block."""
    m = re.search(r"### Suggested Action\s*\n```(?:bash)?\s*\n(.+?)\n```", answer, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def _copy_to_clipboard(text: str) -> bool:
    """Try to copy text to clipboard using available tools."""
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except ImportError:
        pass
    for prog in ["xclip", "xsel", "wl-copy"]:
        try:
            if prog == "xclip":
                p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            elif prog == "xsel":
                p = subprocess.Popen(["xsel", "--clipboard", "--input"], stdin=subprocess.PIPE)
            else:
                p = subprocess.Popen([prog], stdin=subprocess.PIPE)
            p.communicate(input=text.encode(), timeout=2)
            return p.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def _interactive_prompt(answer: str | None, results: list[dict]) -> None:
    """Interactive mode: let user copy/run suggested command or choose from history."""
    suggested = _extract_suggested_command(answer) if answer else None
    commands = []
    if suggested:
        commands.append(("Suggested command", suggested))
    for item in results:
        cmd = item.get("command", "")
        if cmd and cmd != suggested:
            commands.append((f"Historical: {_truncate(item.get('summary', ''), 50)}", cmd))

    if not commands:
        console.print("[dim]No commands available for interactive selection.[/dim]")
        return

    console.print()
    console.print(Panel.fit("[bold]Interactive Mode[/bold]", border_style="cyan"))
    for i, (label, cmd) in enumerate(commands, 1):
        console.print(f"  [bold cyan]{i}.[/bold cyan] {label}")
        console.print(f"     [dim]{cmd}[/dim]")

    raw = Prompt.ask("Select a command number", default="1")
    try:
        idx = int(raw) - 1
        if idx < 0 or idx >= len(commands):
            console.print("[red]Invalid selection.[/red]")
            return
    except ValueError:
        console.print("[red]Invalid selection.[/red]")
        return

    label, selected_cmd = commands[idx]
    console.print()
    console.print(Syntax(selected_cmd, "bash", theme="monokai"))
    console.print()

    action = Prompt.ask(
        "[bold]Action[/bold]",
        choices=["run", "copy", "cancel"],
        default="copy",
    )

    if action == "run":
        confirm = Confirm.ask(f"Run this command?", default=False)
        if confirm:
            console.print(f"[dim]→ Running: {selected_cmd}[/dim]")
            result = subprocess.run(selected_cmd, shell=True, capture_output=True, text=True)
            if result.stdout:
                console.print(result.stdout)
            if result.stderr:
                err_console.print(result.stderr)
            console.print(f"[bold]{'Exit code: ' + str(result.returncode)}[/bold]")
    elif action == "copy":
        if _copy_to_clipboard(selected_cmd):
            console.print("[green]✓ Copied to clipboard.[/green]")
        else:
            console.print(Panel(
                Syntax(selected_cmd, "bash", theme="monokai"),
                title="[bold yellow]Copy this command[/bold yellow]",
                border_style="yellow",
            ))
    else:
        console.print("[dim]Cancelled.[/dim]")


# ── recall ────────────────────────────────────────────────────────
def cmd_recall(args: argparse.Namespace) -> int:
    params: dict[str, str | int | bool] = {
        "query": args.query,
        "limit": args.limit,
    }

    if args.cwd:
        if args.cwd is True:
            import os
            params["cwd"] = os.getcwd()
        else:
            params["cwd"] = str(args.cwd)
    if args.project:
        root = _detect_project_root()
        if root:
            params["project_root"] = root
    if args.category:
        params["category"] = args.category
    if args.failures_only:
        params["failures_only"] = True
    if args.since:
        params["since"] = args.since

    data = _request("GET", "/v1/recall", params=params)

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    results = data.get("results", [])
    answer = data.get("answer")

    if not results and not answer:
        console.print("[dim]Nothing found for that query.[/dim]")
        return 0

    # ── verbose mode: full technical table ─────────────────────────
    if args.verbose:
        if answer:
            console.print()
            console.print(Panel(
                Markdown(answer),
                title="[bold cyan]Synthesis[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            ))
        if results:
            table = Table(
                title=f"Recall: [bold]{args.query}[/bold]",
                show_lines=False,
                padding=(0, 1),
                title_style="bold magenta",
            )
            table.add_column("Timestamp", style="cyan", no_wrap=True)
            table.add_column("Category", style="yellow")
            table.add_column("Command", style="bold white")
            table.add_column("Summary", style="dim white", max_width=45)
            table.add_column("Resolved?", justify="center")
            table.add_column("Score", justify="right")
            for item in results:
                score = float(item.get("score", 0))
                score_text = Text(f"{score:.2f}", style=_score_style(score))
                resolved = item.get("was_resolved")
                resolved_text = "✓" if resolved else ""
                resolved_style = "green" if resolved else "dim"
                table.add_row(
                    _format_ts(item.get("timestamp")),
                    item.get("category", "—"),
                    _truncate(item.get("command", ""), 35),
                    _truncate(item.get("summary", ""), 45),
                    Text(resolved_text, style=resolved_style),
                    score_text,
                )
            console.print()
            console.print(table)
        console.print()
        if args.interactive:
            _interactive_prompt(answer, results)
        return 0

    # ── default: clean conversational output ──────────────────────
    if answer:
        console.print()
        console.print(Markdown(answer))

    if results:
        seen: set[str] = set()
        console.print()
        for item in results:
            cmd = item.get("command", "")
            if cmd not in seen:
                seen.add(cmd)
                label = cmd
                if item.get("was_resolved"):
                    label = f"✓ {cmd}"
                console.print(f"  [bold cyan]{label}[/bold cyan]")
        console.print()

    if args.interactive:
        _interactive_prompt(answer, results)

    return 0


# ── weekly-report ─────────────────────────────────────────────────
def cmd_weekly_report(args: argparse.Namespace) -> int:
    data = _request("GET", "/v1/weekly-report", params={"days": args.days})

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    # Summary stats
    failure_rate = data.get("failure_rate", 0) * 100
    rate_style = "green" if failure_rate < 10 else "yellow" if failure_rate < 30 else "red"
    stats_text = (
        f"[bold]Period:[/bold]  {data.get('period_days', '?')} days\n"
        f"[bold]Events:[/bold] {data.get('total_events', 0)}   "
        f"[bold]Failures:[/bold] {data.get('total_failures', 0)}   "
        f"[bold]Failure rate:[/bold] [{rate_style}]{failure_rate:.1f}%[/{rate_style}]"
    )
    console.print()
    console.print(Panel(stats_text, title="[bold magenta]Weekly Report[/bold magenta]", border_style="magenta"))

    # Category breakdown
    categories = data.get("top_categories", [])
    if categories:
        cat_table = Table(title="Top Categories", show_lines=False, padding=(0, 1), title_style="bold cyan")
        cat_table.add_column("Category", style="yellow")
        cat_table.add_column("Total", justify="right", style="white")
        cat_table.add_column("Failures", justify="right", style="red")
        for cat in categories:
            cat_table.add_row(cat["category"], str(cat["total"]), str(cat["failures"]))
        console.print(cat_table)

    # Recurring failures
    recurring = data.get("recurring_failures", [])
    if recurring:
        console.print()
        console.print("[bold red]Recurring failures:[/bold red]")
        for rf in recurring:
            console.print(f"  [red]•[/red] {rf}")

    console.print()
    return 0


# ── replay-session ────────────────────────────────────────────────
def cmd_replay_session(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {}
    if args.session_id is not None:
        params["session_id"] = args.session_id
    if args.query is not None:
        params["query"] = args.query
    data = _request("GET", "/v1/replay-session", params=params)

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    outcome = data.get("likely_outcome", "unknown")
    outcome_styles = {
        "resolved": "bold green",
        "completed_without_failures": "green",
        "unresolved": "bold red",
    }
    outcome_style = outcome_styles.get(outcome, "yellow")

    header = (
        f"[bold]Session:[/bold]  {data.get('session_id', '?')}   "
        f"[bold]CWD:[/bold] {data.get('cwd', '?')}\n"
        f"[bold]Started:[/bold] {_format_ts(data.get('started_at'))}   "
        f"[bold]Last event:[/bold] {_format_ts(data.get('last_event_at'))}\n"
        f"[bold]Outcome:[/bold] [{outcome_style}]{outcome}[/{outcome_style}]"
    )
    console.print()
    console.print(Panel(header, title="[bold magenta]Session Replay[/bold magenta]", border_style="magenta"))

    steps = data.get("steps", [])
    if steps:
        table = Table(show_lines=False, padding=(0, 1))
        table.add_column("#", justify="right", style="dim")
        table.add_column("Timestamp", style="cyan", no_wrap=True)
        table.add_column("Category", style="yellow")
        table.add_column("Command", style="bold white")
        table.add_column("Exit", justify="center")

        for i, step in enumerate(steps, 1):
            exit_code = step.get("exit_code", 0)
            exit_text = Text(str(exit_code), style=_exit_code_style(exit_code))
            table.add_row(
                str(i),
                _format_ts(step.get("timestamp")),
                step.get("category", "—"),
                _truncate(step.get("command", ""), 50),
                exit_text,
            )
        console.print(table)

    console.print()
    return 0


# ── preflight ─────────────────────────────────────────────────────
def cmd_preflight(args: argparse.Namespace) -> int:
    payload = {"task": args.task, "commands": args.commands or []}
    data = _request("POST", "/v1/preflight", json=payload)

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    warnings = data.get("warnings", [])

    if not warnings:
        console.print("[bold green]✓ No historical warnings found.[/bold green]")
        return 0

    severity_styles = {"high": "bold red", "medium": "yellow", "low": "cyan"}

    console.print()
    console.print(Panel.fit(
        "[bold]Pre-flight Check[/bold]",
        border_style="yellow",
    ))
    for w in warnings:
        sev = w.get("severity", "medium")
        style = severity_styles.get(sev, "yellow")
        label = f"[{style}][{sev.upper()}][/{style}]"
        console.print(f"  {label} {w['message']}")
        evidence = w.get("evidence_event_ids", [])
        if evidence:
            console.print(f"         [dim]evidence: event_ids={evidence}[/dim]")
    console.print()
    return 0


# ── ingest ────────────────────────────────────────────────────────
def cmd_ingest(args: argparse.Namespace) -> int:
    payload = {
        "command": args.command,
        "cwd": args.cwd,
        "output": args.output,
        "exit_code": args.exit_code,
        "duration_ms": args.duration_ms,
    }
    data = _request("POST", "/v1/events", json=payload)

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    console.print(Panel(
        f"[bold]Event ID:[/bold]   {data.get('event_id')}\n"
        f"[bold]Session:[/bold]    {data.get('session_id')}\n"
        f"[bold]Category:[/bold]   {data.get('category')}\n"
        f"[bold]Captured:[/bold]   {_format_ts(data.get('captured_at'))}",
        title="[bold green]✓ Ingested[/bold green]",
        border_style="green",
    ))
    return 0


# ── correct ───────────────────────────────────────────────────────
def cmd_correct(args: argparse.Namespace) -> int:
    if args.category is None and args.root_cause is None:
        err_console.print("[bold red]error:[/bold red] Provide at least one of --category or --root-cause")
        return 1

    payload: dict[str, str] = {}
    if args.category is not None:
        payload["category"] = args.category
    if args.root_cause is not None:
        payload["root_cause"] = args.root_cause

    data = _request("PATCH", f"/v1/events/{args.event_id}/correction", json=payload)

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    changed = []
    if args.category is not None:
        changed.append(f"[bold]Category:[/bold]  [yellow]{data.get('category')}[/yellow]")
    if args.root_cause is not None:
        changed.append(f"[bold]Root Cause:[/bold]  [yellow]{data.get('root_cause')}[/yellow]")

    console.print(Panel(
        f"[bold]Event ID:[/bold]   {data.get('event_id')}\n"
        f"{chr(10).join(changed)}",
        title="[bold green]✓ Corrected[/bold green]",
        border_style="green",
    ))
    return 0


# ── status ────────────────────────────────────────────────────────
def cmd_status(args: argparse.Namespace) -> int:
    try:
        data = _request("GET", "/health")
    except Exception as exc:
        console.print(f"[bold red]✗ Failed to connect to Terminux API:[/bold red] {exc}")
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    console.print()
    console.print(Panel(
        f"[bold]API Status:[/bold]            [green]✓ {data.get('status', 'ok')}[/green]\n"
        f"[bold]SQLite DB Status:[/bold]      [green]✓ Ready[/green] ({'Connected' if data.get('db_ready') else 'Error'})\n"
        f"[bold]Vector Store Status:[/bold]  {'[green]✓ Ready (SQLite)[/green]' if data.get('vector_store_ready') else '[yellow]⚠ Disabled[/yellow]'}\n"
        f"[bold]Active Embeddings:[/bold]     [cyan]{data.get('embedding_backend')}[/cyan] (dim={data.get('embedding_dim')})\n"
        f"[bold]Terminux Version:[/bold]      {data.get('version', '0.1.0')}",
        title="[bold cyan]Terminux System Status[/bold cyan]",
        border_style="cyan",
        expand=False
    ))
    return 0


# ── argument parser ───────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tm", description="Terminux CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    recall = subparsers.add_parser("recall", help="Recall historical issues and fixes")
    recall.add_argument("query", help="Issue/topic to query")
    recall.add_argument("--limit", type=int, default=5)
    recall.add_argument("--verbose", "-v", action="store_true", help="Show full technical details (scores, timestamps, categories)")
    recall.add_argument("--json", action="store_true", help="Output raw JSON")
    recall.add_argument("--cwd", nargs="?", const=True, default=None, help="Scope search to current directory (or a specific path)")
    recall.add_argument("--project", action="store_true", help="Scope search to detected project root")
    recall.add_argument("--category", help="Filter by command category (e.g. container, git-workflow, python-dev)")
    recall.add_argument("--failures-only", action="store_true", dest="failures_only", help="Search only failed commands")
    recall.add_argument("--since", help="Retrieve events within a timeframe (e.g. 1d, 4h, 30m)")
    recall.add_argument("--interactive", "-i", action="store_true", help="Interactive mode: select/copy/run a command")
    recall.set_defaults(func=cmd_recall)

    weekly = subparsers.add_parser("weekly-report", help="Generate weekly operational report")
    weekly.add_argument("--days", type=int, default=7)
    weekly.add_argument("--json", action="store_true", help="Output raw JSON")
    weekly.set_defaults(func=cmd_weekly_report)

    replay = subparsers.add_parser("replay-session", help="Replay a captured terminal session")
    replay.add_argument("--session-id", type=int)
    replay.add_argument("--query")
    replay.add_argument("--json", action="store_true", help="Output raw JSON")
    replay.set_defaults(func=cmd_replay_session)

    preflight = subparsers.add_parser("preflight", help="Warn about historically failing chains")
    preflight.add_argument("task", help="Task description")
    preflight.add_argument("commands", nargs="*", help="Optional command chain")
    preflight.add_argument("--json", action="store_true", help="Output raw JSON")
    preflight.set_defaults(func=cmd_preflight)

    ingest = subparsers.add_parser("ingest", help="Send a sample event to Terminux API")
    ingest.add_argument("command")
    ingest.add_argument("--cwd", default=os.getcwd())
    ingest.add_argument("--output", default="")
    ingest.add_argument("--exit-code", type=int, default=0)
    ingest.add_argument("--duration-ms", type=int)
    ingest.add_argument("--json", action="store_true", help="Output raw JSON")
    ingest.set_defaults(func=cmd_ingest)

    correct = subparsers.add_parser("correct", help="Correct a misclassified event's category or root cause")
    correct.add_argument("event_id", type=int, help="Event ID to correct")
    correct.add_argument("--category", help="New category label")
    correct.add_argument("--root-cause", dest="root_cause", help="New root cause label")
    correct.add_argument("--json", action="store_true", help="Output raw JSON")
    correct.set_defaults(func=cmd_correct)

    status = subparsers.add_parser("status", help="Check backend service health and active model configurations")
    status.add_argument("--json", action="store_true", help="Output raw JSON")
    status.set_defaults(func=cmd_status)

    return parser


def _check_backend() -> None:
    """Quick health-check with a short connect timeout. Raises on failure."""
    url = f"{API_URL}/health"
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)) as client:
            response = client.get(url)
        if response.status_code >= 400:
            raise RuntimeError(f"Backend unhealthy: {response.status_code} {response.text}")
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Could not reach Terminux API at {API_URL}. "
            f"Start the backend first. (error: {exc})"
        ) from exc


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command != "status":
            _check_backend()
        return int(args.func(args))
    except Exception as exc:
        err_console.print(f"[bold red]error:[/bold red] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
