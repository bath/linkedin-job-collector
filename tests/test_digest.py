import os
import stat
import textwrap
from pathlib import Path

import pytest

import digest
from store import Store


def test_provider_commands_use_minimum_default_models(monkeypatch):
    monkeypatch.delenv("LJC_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("LJC_CURSOR_MODEL", raising=False)

    assert digest._provider_command("claude", "prompt") == [
        "claude",
        "-p",
        "--model",
        "haiku",
        "prompt",
    ]
    assert digest._provider_command("cursor", "prompt") == [
        "cursor",
        "agent",
        "-p",
        "--mode",
        "ask",
        "--trust",
        "--model",
        "composer-2.5",
        "prompt",
    ]


def test_parse_response_requires_kept_json():
    raw = """```json
{"kept": ["urn:li:activity:1"]}
```

**Company** - role
"""

    assert digest._parse_response(raw) == (
        ["urn:li:activity:1"],
        "**Company** - role",
    )

    with pytest.raises(digest.DigestProviderError):
        digest._parse_response("No JSON here")


def test_run_digest_falls_back_from_claude_to_cursor(tmp_path, monkeypatch):
    claude = _write_command(
        tmp_path / "fake-claude",
        """
        import sys
        print("weekly limit reached", file=sys.stderr)
        sys.exit(1)
        """,
    )
    cursor = _write_command(
        tmp_path / "fake-cursor",
        """
        print('''```json
{"kept": ["urn:li:activity:keep"]}
```

**Cohere Health** - remote software engineer role
''')
        """,
    )
    monkeypatch.setenv("LJC_DIGEST_PROVIDER", "auto")
    monkeypatch.setenv("LJC_CLAUDE_CMD", str(claude))
    monkeypatch.setenv("LJC_CURSOR_CMD", str(cursor))

    store = Store(tmp_path / "posts.db")
    store.upsert(
        {
            "urn": "urn:li:activity:keep",
            "author": "Hiring Manager",
            "headline": "",
            "text": "Remote Software Engineer role with Python and APIs.",
            "posted_at": None,
            "url": "https://example.com/keep",
        },
        "test",
    )
    store.upsert(
        {
            "urn": "urn:li:activity:drop",
            "author": "Civil Firm",
            "headline": "",
            "text": "Civil Engineer. Non-remote. In office.",
            "posted_at": None,
            "url": "https://example.com/drop",
        },
        "test",
    )

    digest_path = digest.run_digest(store, tmp_path, "20260707T000000Z")

    assert digest_path == tmp_path / "digest-20260707T000000Z.md"
    rows = {
        row["urn"]: row["digest_verdict"]
        for row in store.conn.execute("SELECT urn, digest_verdict FROM posts")
    }
    assert rows == {
        "urn:li:activity:keep": "kept",
        "urn:li:activity:drop": "dropped",
    }


def test_explicit_provider_does_not_fall_back(tmp_path, monkeypatch):
    claude = _write_command(
        tmp_path / "fake-claude",
        """
        import sys
        print("weekly limit reached", file=sys.stderr)
        sys.exit(1)
        """,
    )
    cursor = _write_command(
        tmp_path / "fake-cursor",
        """
        print('''```json
{"kept": ["urn:li:activity:keep"]}
```

**Cohere Health** - remote software engineer role
''')
        """,
    )
    monkeypatch.setenv("LJC_DIGEST_PROVIDER", "claude")
    monkeypatch.setenv("LJC_CLAUDE_CMD", str(claude))
    monkeypatch.setenv("LJC_CURSOR_CMD", str(cursor))

    store = Store(tmp_path / "posts.db")
    store.upsert(
        {
            "urn": "urn:li:activity:keep",
            "author": "Hiring Manager",
            "headline": "",
            "text": "Remote Software Engineer role with Python and APIs.",
            "posted_at": None,
            "url": "https://example.com/keep",
        },
        "test",
    )

    assert digest.run_digest(store, tmp_path, "20260707T000000Z") is None
    row = store.conn.execute("SELECT digest_verdict FROM posts").fetchone()
    assert row["digest_verdict"] is None


def _write_command(path: Path, body: str) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n" + textwrap.dedent(body).strip() + "\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path
