"""Filter newly-captured posts down to genuine remote-SWE hiring posts.

The filter harness is intentionally shell-based: both Claude Code and Cursor
Agent expose stable non-interactive CLIs, and keeping them behind this module
avoids pushing SDK/runtime constraints into the scraper.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from store import Store

PROMPT = (Path(__file__).parent / "prompts" / "filter.md").read_text()
DEFAULT_PROVIDER = "auto"
DEFAULT_CLAUDE_MODEL = "haiku"
DEFAULT_CURSOR_MODEL = "composer-2.5"
VALID_PROVIDERS = ("auto", "claude", "cursor")


class DigestProviderError(Exception):
    """Raised when a provider fails or returns an unusable digest response."""


@dataclass
class ProviderAttempt:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


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

    payload = _build_payload(posts)

    try:
        provider, raw = _run_selected_provider(payload)
        kept_urns, markdown = _parse_response(raw)
    except DigestProviderError as exc:
        print(f"digest: {exc}")
        return None

    # Record verdicts: anything the provider returned as kept = kept, the rest = dropped.
    kept = set(kept_urns)
    for r in rows:
        store.set_verdict(r["urn"], "kept" if r["urn"] in kept else "dropped")

    digest_path = out_dir / f"digest-{ts}.md"
    digest_path.write_text(markdown or raw)
    print(f"digest: {provider}: {len(kept)}/{len(rows)} kept -> {digest_path}")
    return digest_path


def _build_payload(posts: list[dict]) -> str:
    return PROMPT + "\n\n## Posts (JSON)\n```json\n" + json.dumps(posts, indent=2) + "\n```\n"


def _run_selected_provider(payload: str) -> tuple[str, str]:
    requested = os.environ.get("LJC_DIGEST_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if requested not in VALID_PROVIDERS:
        raise DigestProviderError(
            f"unknown LJC_DIGEST_PROVIDER={requested!r}; expected one of {', '.join(VALID_PROVIDERS)}"
        )

    names = ("claude", "cursor") if requested == "auto" else (requested,)
    failures: list[str] = []
    for name in names:
        attempt = _run_provider(name, payload)
        if attempt.returncode == 0:
            raw = attempt.stdout.strip()
            try:
                _parse_response(raw)
            except DigestProviderError as exc:
                failures.append(f"{name} returned an invalid digest: {exc}")
                continue
            return name, raw
        failures.append(_format_attempt_failure(attempt))

    raise DigestProviderError("; ".join(failures))


def _run_provider(name: str, payload: str) -> ProviderAttempt:
    timeout = int(os.environ.get("LJC_DIGEST_TIMEOUT_SECONDS", "300"))
    command = _provider_command(name, payload)
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return ProviderAttempt(
        name=name,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _provider_command(name: str, payload: str) -> list[str]:
    if name == "claude":
        command = shlex.split(os.environ.get("LJC_CLAUDE_CMD", "claude"))
        model = os.environ.get("LJC_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
        return command + ["-p", "--model", model, payload]
    if name == "cursor":
        command = shlex.split(os.environ.get("LJC_CURSOR_CMD", "cursor agent"))
        model = os.environ.get("LJC_CURSOR_MODEL", DEFAULT_CURSOR_MODEL)
        return command + ["-p", "--mode", "ask", "--trust", "--model", model, payload]
    raise DigestProviderError(f"unsupported provider {name!r}")


def _format_attempt_failure(attempt: ProviderAttempt) -> str:
    output = (attempt.stderr or attempt.stdout).strip().replace("\n", " ")
    if len(output) > 500:
        output = output[:500] + "..."
    return f"{attempt.name} failed (rc={attempt.returncode}): {output}"


def _parse_response(raw: str) -> tuple[list[str], str]:
    """Expect a fenced ```json {"kept": [...]} ``` block plus markdown."""
    if "```json" not in raw:
        raise DigestProviderError("missing fenced JSON block")
    try:
        chunk = raw.split("```json", 1)[1].split("```", 1)[0]
        data = json.loads(chunk)
    except (json.JSONDecodeError, IndexError) as exc:
        raise DigestProviderError(f"invalid JSON block: {exc}") from exc

    kept = data.get("kept")
    if not isinstance(kept, list) or not all(isinstance(urn, str) for urn in kept):
        raise DigestProviderError("JSON block must contain a string-list `kept` field")

    md = raw.split("```", 2)[-1].strip() or raw
    return kept, md
