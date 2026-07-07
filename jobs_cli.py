#!/usr/bin/env python3
"""Interactive and agent-friendly entrypoint for ad-hoc job searches."""
from __future__ import annotations

import argparse
import hashlib
import curses
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).parent
BOT = ROOT / "bot.py"
LINKEDIN_CONTENT_SEARCH = "https://www.linkedin.com/search/results/content/"
GITHUB_REPO = "bath/linkedin-job-collector"
GITHUB_API = "https://api.github.com"


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
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "doctor":
        return doctor_main(argv[1:])
    if argv and argv[0] == "update":
        return update_main(argv[1:])

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


def doctor_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="jobs doctor",
        description="Validate that the local jobs collector can run end-to-end.",
    )
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    ap.add_argument(
        "--skip-network",
        action="store_true",
        help="skip GitHub/Cursor/Claude network-auth checks",
    )
    args = ap.parse_args(argv)

    checks = run_doctor_checks(skip_network=args.skip_network)
    ok = all(check["status"] in ("ok", "warn") for check in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, indent=2))
    else:
        for check in checks:
            symbol = {"ok": "✓", "warn": "!", "fail": "✗"}[check["status"]]
            print(f"{symbol} {check['name']}: {check['message']}")
            if check.get("hint"):
                print(f"  hint: {check['hint']}")
    return 0 if ok else 1


def update_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="jobs update",
        description="Download and install the latest linkedin-job-collector release bundle.",
    )
    ap.add_argument("--repo", default=GITHUB_REPO, help="GitHub repo to update from")
    ap.add_argument("--install-dir", default=str(ROOT), help="directory to update")
    ap.add_argument("--dry-run", action="store_true", help="show what would be installed")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = ap.parse_args(argv)

    try:
        plan = build_update_plan(args.repo, Path(args.install_dir))
        if args.dry_run:
            return emit_update_result(plan, args.json, dry_run=True)
        install_release(plan)
        return emit_update_result(plan, args.json, dry_run=False)
    except UpdateError as exc:
        if args.json:
            print(json.dumps({"error": {"code": "update_failed", "message": str(exc)}}))
        else:
            print(f"jobs update: {exc}", file=sys.stderr)
        return 1


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


def run_doctor_checks(skip_network: bool = False) -> list[dict]:
    checks = [
        check_required_files(),
        check_python_runtime(),
        check_python_dependencies(),
        check_playwright_browser(),
        check_data_repo(),
        check_env_file(),
        check_profile_dir(),
        check_jobs_dry_run(),
    ]
    if skip_network:
        checks.append(_check("network checks", "warn", "skipped by --skip-network"))
    else:
        checks.extend(
            [
                check_github_release(),
                check_cursor_agent(),
                check_claude_cli(),
            ]
        )
    return checks


def check_required_files() -> dict:
    required = [
        "jobs",
        "jobs_cli.py",
        "bot.py",
        "digest.py",
        "extract.py",
        "notify.py",
        "store.py",
        "prompts/filter.md",
        "searches.yaml",
    ]
    missing = [path for path in required if not (ROOT / path).exists()]
    if missing:
        return _check("required files", "fail", f"missing {', '.join(missing)}")
    return _check("required files", "ok", "all runtime files are present")


def check_python_runtime() -> dict:
    exe = python_executable()
    if not Path(exe).exists():
        return _check("python runtime", "fail", f"{exe} does not exist", "create the venv with python3 -m venv .venv")
    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    version = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        return _check("python runtime", "fail", f"{exe} failed: {version}")
    return _check("python runtime", "ok", f"using {exe} ({version})")


def check_python_dependencies() -> dict:
    exe = python_executable()
    code = (
        "import importlib.util, json; "
        "mods=['yaml','playwright']; "
        "print(json.dumps([m for m in mods if importlib.util.find_spec(m) is None]))"
    )
    proc = subprocess.run([exe, "-c", code], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return _check("python dependencies", "fail", (proc.stderr or proc.stdout).strip())
    missing = json.loads(proc.stdout)
    if missing:
        return _check(
            "python dependencies",
            "fail",
            f"missing {', '.join(missing)}",
            "run: source .venv/bin/activate && pip install -r requirements.txt && playwright install chromium",
        )
    return _check("python dependencies", "ok", "PyYAML and Playwright imports are available")


def check_playwright_browser() -> dict:
    exe = python_executable()
    code = (
        "from pathlib import Path; "
        "from playwright.sync_api import sync_playwright; "
        "pw=sync_playwright().start(); "
        "path=pw.chromium.executable_path; "
        "pw.stop(); "
        "print(path if Path(path).exists() else '')"
    )
    proc = subprocess.run([exe, "-c", code], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return _check(
            "Playwright Chromium",
            "fail",
            (proc.stderr or proc.stdout).strip(),
            "run: source .venv/bin/activate && playwright install chromium",
        )
    path = proc.stdout.strip()
    if not path:
        return _check("Playwright Chromium", "fail", "Chromium executable not found", "run: playwright install chromium")
    return _check("Playwright Chromium", "ok", f"Chromium is installed at {path}")


def check_data_repo() -> dict:
    data = ROOT / "data"
    db = data / "posts.db"
    if not data.exists():
        return _check("data repo", "fail", "data/ is missing", "clone git@github.com:bath/linkedin-job-data.git data")
    if not db.exists():
        return _check("data repo", "warn", "data/ exists but posts.db is missing", "first scrape will create posts.db")
    return _check("data repo", "ok", "data/ and posts.db are present")


def check_env_file() -> dict:
    env = ROOT / ".env"
    if not env.exists():
        return _check("env file", "warn", ".env is missing", "copy .env.example to .env and fill SMTP/Cursor settings")
    text = env.read_text(errors="ignore")
    configured = [name for name in ("LJC_SMTP_USER", "LJC_SMTP_PASS", "CURSOR_API_KEY") if name in text]
    return _check("env file", "ok", f".env exists ({', '.join(configured) or 'no known keys detected'})")


def check_profile_dir() -> dict:
    profile = ROOT / "profile"
    if not profile.exists():
        return _check("LinkedIn profile", "warn", "profile/ is missing", "first headed run will create it; log into LinkedIn by hand")
    return _check("LinkedIn profile", "ok", "profile/ exists")


def check_jobs_dry_run() -> dict:
    try:
        selection = build_selection("remote-swe", "auto", None, None)
    except Exception as exc:
        return _check("jobs dry run", "fail", f"selection failed: {exc}")
    if "--digest-provider" not in selection["command"]:
        return _check("jobs dry run", "fail", "generated command is missing digest provider")
    return _check("jobs dry run", "ok", "can build a scraper command")


def check_github_release() -> dict:
    try:
        plan = build_update_plan(GITHUB_REPO, ROOT)
    except UpdateError as exc:
        return _check("GitHub release", "fail", str(exc), "check network access and GitHub release assets")
    return _check("GitHub release", "ok", f"latest release {plan.tag} has {plan.asset_name}")


def check_cursor_agent() -> dict:
    cursor = shutil.which("cursor")
    if not cursor:
        return _check("Cursor harness", "warn", "cursor CLI is not on PATH", "install Cursor CLI or use Claude/auto")
    proc = subprocess.run(["cursor", "agent", "status"], capture_output=True, text=True, timeout=30)
    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0 or "Not logged in" in output:
        return _check("Cursor harness", "warn", output or "not logged in", "run: cursor agent login")
    return _check("Cursor harness", "ok", output or "cursor agent is authenticated")


def check_claude_cli() -> dict:
    claude = shutil.which("claude")
    if not claude:
        return _check("Claude harness", "warn", "claude CLI is not on PATH", "install Claude Code or use Cursor")
    proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return _check("Claude harness", "warn", (proc.stderr or proc.stdout).strip(), "check Claude Code auth/quota")
    return _check("Claude harness", "ok", (proc.stdout or proc.stderr).strip())


def _check(name: str, status: str, message: str, hint: str | None = None) -> dict:
    result = {"name": name, "status": status, "message": message}
    if hint:
        result["hint"] = hint
    return result


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class UpdatePlan:
    repo: str
    tag: str
    release_url: str
    asset_name: str
    asset_url: str
    checksum_url: str | None
    install_dir: Path


def build_update_plan(repo: str, install_dir: Path) -> UpdatePlan:
    release = _github_json(f"{GITHUB_API}/repos/{repo}/releases/latest")
    assets = release.get("assets", [])
    tarball = next((asset for asset in assets if asset.get("name", "").endswith(".tar.gz")), None)
    if not tarball:
        raise UpdateError(f"latest release for {repo} has no .tar.gz asset")

    checksum_name = f"{tarball['name']}.sha256"
    checksum = next((asset for asset in assets if asset.get("name") == checksum_name), None)
    return UpdatePlan(
        repo=repo,
        tag=release["tag_name"],
        release_url=release["html_url"],
        asset_name=tarball["name"],
        asset_url=tarball["browser_download_url"],
        checksum_url=checksum["browser_download_url"] if checksum else None,
        install_dir=install_dir.resolve(),
    )


def install_release(plan: UpdatePlan) -> None:
    plan.install_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jobs-update-") as td:
        tmp = Path(td)
        archive = tmp / plan.asset_name
        _download(plan.asset_url, archive)
        if plan.checksum_url:
            checksum = tmp / f"{plan.asset_name}.sha256"
            _download(plan.checksum_url, checksum)
            _verify_checksum(archive, checksum)

        extract_dir = tmp / "extract"
        extract_dir.mkdir()
        _safe_extract(archive, extract_dir)
        roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise UpdateError("release archive must contain exactly one top-level directory")
        _copy_tree_contents(roots[0], plan.install_dir)


def emit_update_result(plan: UpdatePlan, json_output: bool, dry_run: bool) -> int:
    payload = {
        "dry_run": dry_run,
        "repo": plan.repo,
        "tag": plan.tag,
        "release_url": plan.release_url,
        "asset": plan.asset_name,
        "checksum": bool(plan.checksum_url),
        "install_dir": str(plan.install_dir),
    }
    if json_output:
        print(json.dumps(payload, indent=2))
    elif dry_run:
        print(f"Would install {plan.asset_name} from {plan.tag} into {plan.install_dir}")
    else:
        print(f"Installed {plan.asset_name} from {plan.tag} into {plan.install_dir}")
    return 0


def _github_json(url: str) -> dict:
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "linkedin-job-collector"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise UpdateError(f"failed to fetch {url}: {exc}") from exc


def _download(url: str, path: Path) -> None:
    req = Request(url, headers={"User-Agent": "linkedin-job-collector"})
    try:
        with urlopen(req, timeout=60) as resp, path.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except Exception as exc:
        raise UpdateError(f"failed to download {url}: {exc}") from exc


def _verify_checksum(archive: Path, checksum_file: Path) -> None:
    expected = checksum_file.read_text().split()[0]
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        raise UpdateError(f"checksum mismatch for {archive.name}")


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            target = (destination / member.name).resolve()
            if not str(target).startswith(str(destination.resolve()) + os.sep):
                raise UpdateError(f"unsafe archive path: {member.name}")
        tf.extractall(destination)


def _copy_tree_contents(src: Path, dest: Path) -> None:
    for child in src.iterdir():
        target = dest / child.name
        if child.is_dir():
            target.mkdir(exist_ok=True)
            _copy_tree_contents(child, target)
        else:
            shutil.copy2(child, target)


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
