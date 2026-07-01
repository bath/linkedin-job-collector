"""Turn captured RSC / server-driven-UI payloads into post dicts.

LinkedIn migrated content search off the old Voyager JSON API to React Server
Components. The payloads are undocumented and drift; the parser below keys off
stable *semantic* anchors (fsd_update urns, feed-commentary refs, actorName
records) rather than the per-response chunk ids, which are not stable. When
LinkedIn changes things, debug against the raw captures saved in
data/artifacts/ — do NOT re-scrape to iterate on the parser.
"""
from __future__ import annotations

import json
import re


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


# ---------------------------------------------------------------------------
# RSC / SDUI (primary, since LinkedIn migrated content search off Voyager)
# ---------------------------------------------------------------------------
# Content search now renders via React Server Components + server-driven UI.
# Results arrive two ways: the initial SSR page embeds the flight as a JS array
# of JSON-escaped strings (window.__como_rehydration__), and each scroll fetches
# a raw flight payload from /flagship-web/rsc-action/actions/pagination. Both
# decode to the same flight format, parsed here. Anchors used (stable across the
# unstable per-response chunk ids):
#   urn:li:fsd_update:(urn:li:activity:N,...)  — one per post, in render order
#   feed-commentary_<uuid> ... "children":"$L<id>"  — ref to the post body chunk
#   "actorName":"..." ... "activityId":"N" ... "postSlugUrl":"..."  — author+url
FSD_UPDATE = re.compile(r"urn:li:fsd_update:\(urn:li:activity:(\d+)")
COMMENTARY = re.compile(r'feed-commentary_[0-9a-f-]+".*?"children":"(\$L?[0-9a-f]+)"', re.S)
ACTOR = re.compile(
    r'"actorName":"((?:[^"\\]|\\.)*)".{0,400}?"activityId":"(\d+)"'
    r'.{0,400}?"postSlugUrl":"((?:[^"\\]|\\.)*)"',
    re.S,
)


def _rsc_unescape(s: str) -> str:
    try:
        return json.loads('"' + s + '"')
    except (json.JSONDecodeError, ValueError):
        return s


def _flight_from_html(html: str) -> str:
    """The SSR page embeds flight as `window.__como_rehydration__ = ["1:I[...", ...]`
    — a JS array of JSON-escaped strings. Decode and join back to raw flight."""
    i = html.find("__como_rehydration__")
    if i < 0:
        return ""
    start = html.find("[", i)
    if start < 0:
        return ""
    depth, j, in_str, esc = 0, start, False, False
    while j < len(html):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    try:
        parts = json.loads(html[start:j + 1])
        return "".join(p for p in parts if isinstance(p, str))
    except (json.JSONDecodeError, ValueError):
        return ""


def _flight_chunks(flight: str) -> dict:
    """Map flight chunk id -> parsed JSON value (array/object chunks only)."""
    out: dict[str, object] = {}
    for line in flight.split("\n"):
        m = re.match(r"^([0-9a-f]+):(.*)$", line, re.S)
        if m and m.group(2)[:1] in ("[", "{"):
            try:
                out[m.group(1)] = json.loads(m.group(2))
            except (json.JSONDecodeError, ValueError):
                pass
    return out


def _collect_text(node, chunks: dict, seen=None, depth=0) -> list:
    """Walk a resolved flight node, returning ordered text fragments and
    resolving '$L<id>'/'$<id>' references against the chunk map."""
    if seen is None:
        seen = set()
    if depth > 60:
        return []
    out: list[str] = []
    if isinstance(node, str):
        if node.startswith("$L") or re.fullmatch(r"\$[0-9a-f]+", node or ""):
            ref = node[2:] if node.startswith("$L") else node[1:]
            if ref in chunks and ref not in seen:
                seen.add(ref)
                out += _collect_text(chunks[ref], chunks, seen, depth + 1)
        elif not node.startswith("$"):
            out.append(node)
    elif isinstance(node, list):
        if len(node) == 4 and node[0] == "$":  # React element ["$", type, key, props]
            if node[1] == "br":
                out.append("\n")
            out += _collect_text(node[3], chunks, seen, depth + 1)
        else:
            for it in node:
                out += _collect_text(it, chunks, seen, depth + 1)
    elif isinstance(node, dict):
        # Follow only content-bearing keys, so we skip classNames/style/enums.
        for key in ("textProps", "children", "text", "placeholder", "title"):
            if key in node:
                out += _collect_text(node[key], chunks, seen, depth + 1)
    return out


def from_rsc(raw: str) -> list[dict]:
    """Extract posts from one RSC payload (SSR HTML page or pagination flight)."""
    flight = raw if ("fsd_update" in raw and not raw.lstrip().startswith("<")) else _flight_from_html(raw)
    if not flight:
        return []
    chunks = _flight_chunks(flight)
    ids = [m.group(1) for m in FSD_UPDATE.finditer(flight)]              # render order
    body_refs = list(dict.fromkeys(m.group(1) for m in COMMENTARY.finditer(flight)))
    actor = {m.group(2): (_rsc_unescape(m.group(1)), _rsc_unescape(m.group(3)))
             for m in ACTOR.finditer(flight)}
    posts = []
    for i, aid in enumerate(ids):
        urn = f"urn:li:activity:{aid}"
        text = ""
        if i < len(body_refs):
            ref = body_refs[i]
            rid = ref[2:] if ref.startswith("$L") else ref[1:]
            text = re.sub(r"\n{3,}", "\n\n",
                          "".join(_collect_text(chunks.get(rid, ""), chunks))).strip()
        author, url = actor.get(aid, (None, post_url(urn)))
        posts.append({"urn": urn, "author": author, "headline": None,
                      "text": text, "posted_at": None, "url": url})
    return posts


def from_rsc_many(payloads: list[str]) -> list[dict]:
    """Extract + dedupe posts across many captured RSC payloads."""
    out: dict[str, dict] = {}
    for raw in payloads:
        for p in from_rsc(raw):
            if p["text"] or p["author"]:
                out.setdefault(p["urn"], p)
    return list(out.values())


def load_rsc_files(paths: list[str]) -> list[str]:
    """Load raw RSC payload captures (one payload per file) for offline reparse."""
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                out.append(fh.read())
        except OSError:
            continue
    return out
