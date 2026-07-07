#!/usr/bin/env python3
"""Interactive and agent-friendly entrypoint for ad-hoc job searches."""
from __future__ import annotations

import argparse
import curses
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).parent
BOT = ROOT / "bot.py"
LINKEDIN_CONTENT_SEARCH = "https://www.linkedin.com/search/results/content/"


@dataclass(frozen=True)
class QueryOption:
    id: str
    label: str
    url: str | None
    description: str


@dataclass(frozen=True)
class HarnessOption:
    id: str
    label: str
    description: str


def linkedin_search_url(keywords: str) -> str:
    return (
        f"{LINKEDIN_CONTENT_SEARCH}?keywords={quote(keywords)}"
        "&origin=GLOBAL_SEARCH_HEADER&datePosted=%5B%22past-week%22%5D"
    )


def python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


QUERY_OPTIONS = [
    QueryOption(
        id="remote-swe",
        label="Remote software engineer",
        url=linkedin_search_url("hiring software engineer remote"),
        description="General remote SWE hiring posts from the past week.",
    ),
    QueryOption(
        id="remote-platform",
        label="Remote platform/backend engineer",
        url=linkedin_search_url("hiring platform backend software engineer remote"),
        description="Backend, platform, infrastructure, and API-heavy roles.",
    ),
    QueryOption(
        id="remote-data",
        label="Remote data platform engineer",
        url=linkedin_search_url("hiring data platform software engineer remote"),
        description="Data platform, pipelines, analytics infra, Airflow/dbt style roles.",
    ),
    QueryOption(
        id="custom-query",
        label="Custom LinkedIn keyword search",
        url=None,
        description="Build a LinkedIn content search URL from your own keywords.",
    ),
    QueryOption(
        id="custom-url",
        label="Custom LinkedIn content URL",
        url=None,
        description="Paste a full LinkedIn content search URL.",
    ),
]

HARNESS_OPTIONS = [
    HarnessOption("auto", "Auto", "Try Claude, then Cursor if Claude fails."),
    HarnessOption("claude", "Claude", "Force Claude Code digest filtering."),
    HarnessOption("cursor", "Cursor (Composer 2.5)", "Force Cursor Agent with the Composer 2.5 default."),
]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        return emit_selection(
            build_selection(args.query, args.harness, args.custom_query, args.custom_url),
            json_output=args.json,
            dry_run=True,
        )

    if args.query or args.custom_query or args.custom_url:
        selection = build_selection(args.query, args.harness, args.custom_query, args.custom_url)
    elif sys.stdin.isatty() and sys.stdout.isatty():
        selection = run_tui()
    else:
        print(
            "jobs: noninteractive use requires --query, --custom-query, or --custom-url. "
            "Try: jobs --query remote-swe --harness auto --dry-run --json",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        return emit_selection(selection, json_output=args.json, dry_run=True)
    return run_bot(selection)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="jobs",
        description="Choose a LinkedIn job search and digest harness, then run the collector.",
    )
    ap.add_argument("--query", choices=[opt.id for opt in QUERY_OPTIONS if opt.url], help="prebuilt search")
    ap.add_argument("--custom-query", help="custom LinkedIn keywords to turn into a content search URL")
    ap.add_argument("--custom-url", help="full LinkedIn content search URL")
    ap.add_argument("--harness", choices=[opt.id for opt in HARNESS_OPTIONS], default="auto")
    ap.add_argument("--dry-run", action="store_true", help="print what would run without scraping")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    ap.add_argument("--list", action="store_true", help="list available options and exit")
    return ap.parse_args(argv)


def build_selection(
    query_id: str | None,
    harness_id: str,
    custom_query: str | None,
    custom_url: str | None,
) -> dict:
    if custom_url:
        query = QueryOption("custom-url", "Custom LinkedIn content URL", custom_url, "User-provided URL.")
    elif custom_query:
        query = QueryOption(
            "custom-query",
            f"Custom: {custom_query}",
            linkedin_search_url(custom_query),
            "User-provided keyword search.",
        )
    else:
        query = next((opt for opt in QUERY_OPTIONS if opt.id == (query_id or "remote-swe")), None)
        if query is None or query.url is None:
            raise SystemExit(f"jobs: unknown or incomplete query {query_id!r}")

    harness = next((opt for opt in HARNESS_OPTIONS if opt.id == harness_id), None)
    if harness is None:
        raise SystemExit(f"jobs: unknown harness {harness_id!r}")

    command = [
        python_executable(),
        str(BOT),
        "--search-name",
        query.id,
        "--search-url",
        query.url,
        "--digest-provider",
        harness.id,
    ]
    return {
        "query": asdict(query),
        "harness": asdict(harness),
        "command": command,
    }


def emit_selection(selection: dict, json_output: bool, dry_run: bool) -> int:
    if json_output:
        payload = {
            "dry_run": dry_run,
            "selection": selection,
            "available": {
                "queries": [asdict(opt) for opt in QUERY_OPTIONS],
                "harnesses": [asdict(opt) for opt in HARNESS_OPTIONS],
            },
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Query:   {selection['query']['label']}")
    print(f"Harness: {selection['harness']['label']}")
    print("Command:")
    print("  " + " ".join(selection["command"]))
    return 0


def run_bot(selection: dict) -> int:
    print(f"Running {selection['query']['label']} with {selection['harness']['label']} harness...")
    return subprocess.run(selection["command"], cwd=ROOT).returncode


def run_tui() -> dict:
    return curses.wrapper(_run_tui)


def _run_tui(stdscr) -> dict:
    curses.curs_set(0)
    query_idx = select_option(stdscr, "What type of job should we query?", QUERY_OPTIONS)
    query = QUERY_OPTIONS[query_idx]

    custom_query = None
    custom_url = None
    if query.id == "custom-query":
        custom_query = prompt_text(stdscr, "Custom LinkedIn keywords")
    elif query.id == "custom-url":
        custom_url = prompt_text(stdscr, "LinkedIn content search URL")

    harness_idx = select_option(stdscr, "Which harness should filter the digest?", HARNESS_OPTIONS)
    harness = HARNESS_OPTIONS[harness_idx]
    return build_selection(query.id, harness.id, custom_query, custom_url)


def select_option(stdscr, title: str, options: list[QueryOption] | list[HarnessOption]) -> int:
    idx = 0
    while True:
        stdscr.erase()
        stdscr.addstr(0, 0, title)
        stdscr.addstr(1, 0, "Use ↑/↓ or j/k, Enter to select, q to quit.")
        for i, option in enumerate(options):
            marker = ">" if i == idx else " "
            stdscr.addstr(3 + i, 0, f"{marker} {option.label}")
            stdscr.addstr(3 + i, 32, option.description[: max(0, curses.COLS - 34)])
        key = stdscr.getch()
        if key in (ord("q"), 27):
            raise SystemExit(130)
        if key in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = min(len(options) - 1, idx + 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            return idx


def prompt_text(stdscr, label: str) -> str:
    curses.curs_set(1)
    stdscr.erase()
    stdscr.addstr(0, 0, f"{label}: ")
    stdscr.refresh()
    curses.echo()
    value = stdscr.getstr(0, len(label) + 2).decode("utf-8").strip()
    curses.noecho()
    curses.curs_set(0)
    if not value:
        raise SystemExit(f"jobs: {label} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
