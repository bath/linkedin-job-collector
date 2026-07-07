#!/usr/bin/env python3
"""Smoke-test the digest provider workflow without scraping LinkedIn."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digest import run_digest  # noqa: E402
from store import Store  # noqa: E402


SAMPLE_POSTS = [
    {
        "urn": "urn:li:activity:smoke-remote-data-platform",
        "author": "Cohere Health hiring manager",
        "headline": "",
        "text": (
            "We're hiring a Software Engineer II, Data Platform. Remote-first US role. "
            "Python, SQL, Airflow, dbt, AWS, APIs, healthcare data."
        ),
        "posted_at": None,
        "url": "https://example.com/remote-data-platform",
    },
    {
        "urn": "urn:li:activity:smoke-remote-lead-swe",
        "author": "PracticeTek recruiter",
        "headline": "",
        "text": (
            "Hiring a Lead Software Engineer for a remote SaaS role. "
            ".NET/C#, Angular, cloud, CI/CD, observability."
        ),
        "posted_at": None,
        "url": "https://example.com/remote-lead-swe",
    },
    {
        "urn": "urn:li:activity:smoke-civil-nonremote",
        "author": "Civil engineering firm",
        "headline": "",
        "text": "Civil Engineer, land development. In-person only. This is NOT a remote position.",
        "posted_at": None,
        "url": "https://example.com/civil",
    },
    {
        "urn": "urn:li:activity:smoke-staff-augmentation",
        "author": "Staff augmentation vendor",
        "headline": "",
        "text": (
            "What if your next software engineer was in Bangladesh? Read our article "
            "about remote staff augmentation for European companies."
        ),
        "posted_at": None,
        "url": "https://example.com/staff-augmentation",
    },
]

MUST_KEEP = {
    "urn:li:activity:smoke-remote-data-platform",
    "urn:li:activity:smoke-remote-lead-swe",
}
MUST_DROP = {
    "urn:li:activity:smoke-civil-nonremote",
    "urn:li:activity:smoke-staff-augmentation",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--provider",
        choices=("auto", "claude", "cursor"),
        default=os.environ.get("LJC_DIGEST_PROVIDER", "auto"),
        help="Provider to smoke-test. Defaults to LJC_DIGEST_PROVIDER or auto.",
    )
    args = ap.parse_args()

    old_provider = os.environ.get("LJC_DIGEST_PROVIDER")
    os.environ["LJC_DIGEST_PROVIDER"] = args.provider

    try:
        with tempfile.TemporaryDirectory(prefix="ljc-digest-smoke-") as td:
            root = Path(td)
            store = Store(root / "posts.db")
            for post in SAMPLE_POSTS:
                store.upsert(post, "smoke")

            digest_path = run_digest(store, root, "smoke")
            if digest_path is None:
                print(f"smoke: provider={args.provider} failed to produce a digest", file=sys.stderr)
                return 1

            verdicts = _verdicts(store.conn)
            missing_keep = sorted(urn for urn in MUST_KEEP if verdicts.get(urn) != "kept")
            missing_drop = sorted(urn for urn in MUST_DROP if verdicts.get(urn) != "dropped")
            if missing_keep or missing_drop:
                print(f"smoke: provider={args.provider} produced lacking results", file=sys.stderr)
                print(f"  expected kept but did not keep: {missing_keep}", file=sys.stderr)
                print(f"  expected dropped but did not drop: {missing_drop}", file=sys.stderr)
                print(digest_path.read_text(), file=sys.stderr)
                return 2

            print(f"smoke: provider={args.provider} nominal -> {digest_path}")
            return 0
    finally:
        if old_provider is None:
            os.environ.pop("LJC_DIGEST_PROVIDER", None)
        else:
            os.environ["LJC_DIGEST_PROVIDER"] = old_provider


def _verdicts(conn: sqlite3.Connection) -> dict[str, str | None]:
    return {
        row["urn"]: row["digest_verdict"]
        for row in conn.execute("SELECT urn, digest_verdict FROM posts")
    }


if __name__ == "__main__":
    raise SystemExit(main())
