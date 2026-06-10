"""Turn captured Voyager JSON (primary) or saved HTML (fallback) into post dicts.

LinkedIn's Voyager payloads are undocumented and drift. The shapes below are
written defensively: we walk the JSON looking for objects that carry an
activity URN plus actor/commentary, rather than assuming fixed key paths. When
LinkedIn changes things, debug against the raw captures saved in
data/artifacts/ — do NOT re-scrape to iterate on the parser.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

ACTIVITY_URN = re.compile(r"urn:li:activity:\d+")


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


# ---------------------------------------------------------------------------
# Voyager JSON (primary)
# ---------------------------------------------------------------------------

def _walk(obj) -> Iterable[dict]:
    """Yield every dict nested anywhere in a JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _first_str(d: dict, *keys) -> str | None:
    """Best-effort: pull a string out of common LinkedIn text wrappers."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            # text nodes look like {"text": "..."} or {"text": {"text": "..."}}
            inner = v.get("text")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
            if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                return inner["text"].strip()
    return None


def from_voyager(payloads: list[dict]) -> list[dict]:
    """Extract posts from a list of parsed Voyager JSON response bodies."""
    out: dict[str, dict] = {}
    for payload in payloads:
        for node in _walk(payload):
            urn = _find_activity_urn(node)
            if not urn:
                continue
            text = _first_str(node, "commentary", "text", "summary")
            actor = node.get("actor") if isinstance(node.get("actor"), dict) else {}
            author = _first_str(actor, "name", "title") if actor else None
            headline = _first_str(actor, "description", "subtitle") if actor else None
            posted = _first_str(actor, "subDescription") if actor else None
            # Only keep nodes that actually look like a post (have text or actor).
            if not (text or author):
                continue
            out.setdefault(
                urn,
                {
                    "urn": urn,
                    "author": author,
                    "headline": headline,
                    "text": text,
                    "posted_at": posted,
                    "url": post_url(urn),
                },
            )
    return list(out.values())


def _find_activity_urn(node: dict) -> str | None:
    for key in ("entityUrn", "*socialDetail", "updateMetadata", "backendUrn", "preDashEntityUrn"):
        v = node.get(key)
        if isinstance(v, str):
            m = ACTIVITY_URN.search(v)
            if m:
                return m.group(0)
    # last resort: any string field on this node carrying an activity urn
    for v in node.values():
        if isinstance(v, str):
            m = ACTIVITY_URN.search(v)
            if m:
                return m.group(0)
    return None


# ---------------------------------------------------------------------------
# HTML (fallback / audit reparse)
# ---------------------------------------------------------------------------

def from_html(html: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, dict] = {}
    for div in soup.select("[data-urn]"):
        urn = div.get("data-urn", "")
        if not ACTIVITY_URN.fullmatch(urn or ""):
            continue

        def _text(sel):
            el = div.select_one(sel)
            return el.get_text(" ", strip=True) if el else None

        out.setdefault(
            urn,
            {
                "urn": urn,
                "author": _text(".update-components-actor__title"),
                "headline": _text(".update-components-actor__description"),
                "text": _text(".update-components-text"),
                "posted_at": _text(".update-components-actor__sub-description"),
                "url": post_url(urn),
            },
        )
    return list(out.values())


def load_voyager_files(paths: list[str]) -> list[dict]:
    payloads = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                payloads.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            continue
    return payloads
