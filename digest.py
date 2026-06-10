"""Filter newly-captured posts down to genuine remote-SWE hiring posts via
`claude -p`, write a ranked markdown digest, and record verdicts in the DB.

Uses the Claude Code CLI (your existing auth) rather than the Anthropic SDK, so
there's no API key to manage.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from store import Store

PROMPT = (Path(__file__).parent / "prompts" / "filter.md").read_text()


def run_digest(store: Store, out_dir: Path, ts: str) -> Path | None:
    rows = store.unjudged()
    if not rows:
        print("digest: no unjudged posts")
        return None

    posts = [
        {
            "urn": r["urn"],
            "author": r["author"],
            "headline": r["headline"],
            "text": r["text"],
            "url": r["url"],
        }
        for r in rows
    ]

    payload = PROMPT + "\n\n## Posts (JSON)\n```json\n" + json.dumps(posts, indent=2) + "\n```\n"

    proc = subprocess.run(
        ["claude", "-p", payload],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        print(f"digest: claude -p failed (rc={proc.returncode}): {proc.stderr[:500]}")
        return None

    raw = proc.stdout.strip()
    kept_urns, markdown = _parse_response(raw)

    # Record verdicts: anything claude returned as kept = kept, the rest = dropped.
    kept = set(kept_urns)
    for r in rows:
        store.set_verdict(r["urn"], "kept" if r["urn"] in kept else "dropped")

    digest_path = out_dir / f"digest-{ts}.md"
    digest_path.write_text(markdown or raw)
    print(f"digest: {len(kept)}/{len(rows)} kept -> {digest_path}")
    return digest_path


def _parse_response(raw: str) -> tuple[list[str], str]:
    """Expect claude to emit a fenced ```json {"kept": [...]} ``` block plus
    human-readable markdown. Tolerate it returning only one of the two."""
    kept: list[str] = []
    md = raw
    if "```json" in raw:
        try:
            chunk = raw.split("```json", 1)[1].split("```", 1)[0]
            data = json.loads(chunk)
            kept = data.get("kept", [])
            md = raw.split("```", 2)[-1].strip() or raw
        except (json.JSONDecodeError, IndexError):
            pass
    return kept, md
