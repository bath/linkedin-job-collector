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
import random
import re
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

# Content-search results now arrive as React Server Components / server-driven UI
# (LinkedIn migrated off Voyager). Two carriers: the initial SSR document at
# /search/results/content/ (flight embedded in the HTML) and each scroll's
# pagination fetch. We keep response bodies that carry post updates. See
# extract.from_rsc for the format. LinkedIn moves these — match loosely.
RSC_HINTS = ("/search/results/content/", "rsc-action/actions/pagination")


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
    captured: list[str] = []

    def on_response(resp):
        try:
            if any(h in resp.url for h in RSC_HINTS):
                body = resp.text()
                if "fsd_update" in body or "actorName" in body:
                    captured.append(body)
        except Exception:
            pass  # non-JSON, streamed, or aborted — ignore

    page.on("response", on_response)
    print(f"[{search['name']}] navigating")
    page.goto(search["url"], wait_until="domcontentloaded")
    wait_for_login(page, unattended=unattended)

    # Content-search results load lazily, several seconds behind domcontentloaded
    # (worst on the first cold hit right after login). Give the results list a
    # chance to render before scrolling, so we don't bail on an empty shell.
    # Selector drift is expected — this is best-effort, not required.
    try:
        page.wait_for_selector(
            "div.search-results-container, .scaffold-finite-scroll__content, "
            "div.search-results__list, ul[role='list'] li",
            timeout=load.get("results_wait_ms", 20000),
        )
    except Exception:
        pass
    _jitter(load["min_wait_ms"], load["max_wait_ms"])

    seen_count = 0
    dry = 0
    for i in range(load["max_iterations"]):
        # Each scroll triggers a pagination fetch (~3 posts). Full post text is
        # in the payload, so no need to click "see more" on individual posts.
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

        posts = extract.from_rsc_many(captured)
        print(f"  scroll {i + 1}/{load['max_iterations']}: {len(posts)} posts captured")
        if len(posts) >= load["target_posts"]:
            break
        if len(posts) == seen_count:
            # Don't count "dry" scrolls until at least one post has appeared —
            # otherwise a slow first render trips the early-stop before any
            # results exist. Once posts are in, a flat count means we're done.
            if seen_count > 0:
                dry += 1
                if dry >= load["dry_scrolls_to_stop"]:
                    print("  no new posts; stopping early")
                    break
        else:
            dry = 0
        seen_count = len(posts)

    page.remove_listener("response", on_response)

    # Persist raw RSC captures for offline reparse / audit trail (one per file).
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for idx, body in enumerate(captured):
        (artifact_dir / f"{search['name']}.rsc-{idx:02d}.txt").write_text(body, encoding="utf-8")

    posts = extract.from_rsc_many(captured)
    if not posts:
        print("  RSC yielded nothing; saving page HTML for debugging")
        (artifact_dir / f"{search['name']}.debug.html").write_text(page.content(), encoding="utf-8")
    return posts


def reparse(artifact_subdir: str) -> None:
    """Rebuild posts from saved captures without touching LinkedIn."""
    d = Path(artifact_subdir)
    store = Store(DB_PATH)
    total_new = 0
    # Group RSC captures by search name (e.g. hiring-remote-swe.rsc-00.txt).
    by_search: dict[str, list[str]] = {}
    for rf in sorted(d.glob("*.rsc-*.txt")):
        name = re.sub(r"\.rsc-\d+\.txt$", "", rf.name)
        by_search.setdefault(name, []).append(str(rf))
    for name, paths in by_search.items():
        posts = extract.from_rsc_many(extract.load_rsc_files(paths))
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
