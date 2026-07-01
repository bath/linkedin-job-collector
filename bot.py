#!/usr/bin/env python3
"""linkedin-job-collector — scrape LinkedIn content search, dedupe into SQLite,
emit a Claude-filtered digest.

Usage:
    python bot.py            # run all searches in searches.yaml
    python bot.py --no-digest    # scrape + store only, skip the claude filter
    python bot.py --reparse data/artifacts/<ts>   # rebuild posts from saved captures

Data (DB + artifacts) is written under data/, which is a SEPARATE private repo
cloned into this directory. Nothing under data/ is committed to this public repo.

Account safety: uses a persistent browser profile (profile/), runs headed, never
automates login, and waits at the login wall for you to sign in by hand.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

import extract
from store import Store

ROOT = Path(__file__).parent
DATA = ROOT / "data"
ARTIFACTS = DATA / "artifacts"
PROFILE = ROOT / "profile"
DB_PATH = DATA / "posts.db"

# Voyager endpoints that carry content-search results. LinkedIn moves these;
# we match loosely on substrings and keep every body that parses as JSON.
VOYAGER_HINTS = ("/voyager/api/graphql", "/voyager/api/search", "search/dash/clusters")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _jitter(lo_ms: int, hi_ms: int) -> None:
    time.sleep(random.uniform(lo_ms, hi_ms) / 1000.0)


def load_config() -> dict:
    return yaml.safe_load((ROOT / "searches.yaml").read_text())


def ensure_data_repo() -> None:
    if not DATA.exists():
        sys.exit(
            "data/ not found. Clone your private data repo into it first:\n"
            "  git clone git@github.com:bath/linkedin-job-data.git data\n"
            "(see README). Refusing to write scraped data into the public repo."
        )
    ARTIFACTS.mkdir(parents=True, exist_ok=True)


class LoginRequired(Exception):
    """Raised in unattended mode when the session has expired and a human must
    sign in. Lets main() email a re-auth alert and exit cleanly."""


def _at_login_wall(page) -> bool:
    url = page.url
    return "/login" in url or "/checkpoint" in url or "/authwall" in url


def wait_for_login(page, unattended: bool = False) -> None:
    """Handle the auth wall. Interactive: leave the window open and wait for the
    user to sign in. Unattended (scheduled): don't hang for 10 minutes on a dead
    session — raise so the caller can email a re-auth alert and exit."""
    if unattended:
        if _at_login_wall(page):
            raise LoginRequired
        return
    for _ in range(120):  # up to ~10 min
        if _at_login_wall(page):
            print("  -> login required. Sign in in the browser window; waiting...")
            _jitter(5000, 5000)
            continue
        return
    sys.exit("login not completed in time; aborting.")


def scrape_search(
    page, search: dict, load: dict, artifact_dir: Path, unattended: bool = False
) -> list[dict]:
    captured: list[dict] = []

    def on_response(resp):
        try:
            if any(h in resp.url for h in VOYAGER_HINTS):
                body = resp.json()
                captured.append(body)
        except Exception:
            pass  # non-JSON, streamed, or aborted — ignore

    page.on("response", on_response)
    print(f"[{search['name']}] navigating")
    page.goto(search["url"], wait_until="domcontentloaded")
    wait_for_login(page, unattended=unattended)
    _jitter(load["min_wait_ms"], load["max_wait_ms"])

    seen_count = 0
    dry = 0
    for i in range(load["max_iterations"]):
        # Expand truncated post bodies so HTML fallback captures full text.
        for btn in page.query_selector_all(
            "button.feed-shared-inline-show-more-text__see-more-less-toggle"
        ):
            try:
                btn.click(timeout=500)
            except Exception:
                pass

        page.mouse.wheel(0, 4000)
        _jitter(load["min_wait_ms"], load["max_wait_ms"])

        # "Show more results" button appears after the first couple of batches.
        more = page.query_selector("button.scaffold-finite-scroll__load-button")
        if more:
            try:
                more.click()
                _jitter(load["min_wait_ms"], load["max_wait_ms"])
            except Exception:
                pass

        posts = extract.from_voyager(captured)
        print(f"  scroll {i + 1}/{load['max_iterations']}: {len(posts)} posts captured")
        if len(posts) >= load["target_posts"]:
            break
        if len(posts) == seen_count:
            dry += 1
            if dry >= load["dry_scrolls_to_stop"]:
                print("  no new posts; stopping early")
                break
        else:
            dry = 0
        seen_count = len(posts)

    page.remove_listener("response", on_response)

    # Persist raw artifacts for offline reparse / audit trail.
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"{search['name']}.html").write_text(page.content(), encoding="utf-8")
    (artifact_dir / f"{search['name']}.voyager.json").write_text(
        json.dumps(captured), encoding="utf-8"
    )

    posts = extract.from_voyager(captured)
    if not posts:
        print("  voyager yielded nothing; falling back to HTML parse")
        posts = extract.from_html(page.content())
    return posts


def reparse(artifact_subdir: str) -> None:
    """Rebuild posts from saved captures without touching LinkedIn."""
    d = Path(artifact_subdir)
    store = Store(DB_PATH)
    total_new = 0
    for vf in d.glob("*.voyager.json"):
        name = vf.name.replace(".voyager.json", "")
        payloads = extract.load_voyager_files([str(vf)])
        posts = extract.from_voyager(payloads)
        if not posts:
            html = (d / f"{name}.html")
            if html.exists():
                posts = extract.from_html(html.read_text(encoding="utf-8"))
        for p in posts:
            if store.upsert(p, name):
                total_new += 1
    store.close()
    print(f"reparse: {total_new} new posts from {d}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-digest", action="store_true", help="skip the claude filter step")
    ap.add_argument("--reparse", metavar="DIR", help="rebuild from a saved artifacts dir, no scraping")
    ap.add_argument(
        "--unattended",
        action="store_true",
        help="scheduled mode: don't wait at the login wall — email a re-auth alert and exit",
    )
    args = ap.parse_args()

    if args.reparse:
        reparse(args.reparse)
        return

    ensure_data_repo()
    cfg = load_config()
    load = cfg["load"]
    ts = _ts()
    artifact_dir = ARTIFACTS / ts

    from playwright.sync_api import sync_playwright

    import notify

    store = Store(DB_PATH)
    total_new = 0
    login_required = False
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for search in cfg["searches"]:
                posts = scrape_search(page, search, load, artifact_dir, unattended=args.unattended)
                new = sum(1 for p in posts if store.upsert(p, search["name"]))
                total_new += new
                print(f"[{search['name']}] {len(posts)} posts, {new} new")
                _jitter(load["min_wait_ms"], load["max_wait_ms"])
        except LoginRequired:
            login_required = True
            print("session expired; unattended run aborting. Sending re-auth alert.")
        ctx.close()

    if login_required:
        notify.send_reauth_alert()
        store.close()
        sys.exit(75)  # EX_TEMPFAIL: nothing scraped, retry after a manual login

    print(f"\nstored {total_new} new posts; artifacts -> {artifact_dir}")

    if not args.no_digest and total_new:
        from digest import run_digest

        run_digest(store, DATA, ts)

    # Always attempt notification: emails kept posts not yet sent, including any
    # left pending by an earlier failed send. No-ops when there's nothing new.
    sent = notify.notify_new_matches(store)
    if sent:
        print(f"notify: emailed {sent} new match(es)")
    store.close()


if __name__ == "__main__":
    main()
