"""SQLite persistence. The DB lives in the private data repo (data/posts.db).

The code in this repo is throwaway; the accumulated data is not. Schema is the
durable contract — if you rewrite the bot, keep this table shape.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    urn            TEXT PRIMARY KEY,   -- urn:li:activity:... (dedupe key)
    author         TEXT,
    headline       TEXT,
    text           TEXT,
    posted_at      TEXT,               -- relative as scraped ("3d", "1w") or ISO if known
    url            TEXT,
    search_name    TEXT,               -- which searches.yaml entry surfaced it
    first_seen     TEXT NOT NULL,      -- ISO8601 UTC, when we first captured it
    digest_verdict TEXT,               -- filled by digest.py: kept | dropped | NULL (unjudged)
    notified_at    TEXT                -- ISO8601 UTC when a kept post was emailed; NULL = not yet
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns to DBs created before they existed. Idempotent."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(posts)")}
        if "notified_at" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN notified_at TEXT")

    def upsert(self, post: dict, search_name: str) -> bool:
        """Insert a post if its URN is new. Returns True if newly inserted."""
        existing = self.conn.execute(
            "SELECT 1 FROM posts WHERE urn = ?", (post["urn"],)
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """INSERT INTO posts
               (urn, author, headline, text, posted_at, url, search_name, first_seen)
               VALUES (:urn, :author, :headline, :text, :posted_at, :url, :search_name, :first_seen)""",
            {
                "urn": post["urn"],
                "author": post.get("author"),
                "headline": post.get("headline"),
                "text": post.get("text"),
                "posted_at": post.get("posted_at"),
                "url": post.get("url"),
                "search_name": search_name,
                "first_seen": _now(),
            },
        )
        self.conn.commit()
        return True

    def unjudged(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM posts WHERE digest_verdict IS NULL ORDER BY first_seen"
        ).fetchall()

    def set_verdict(self, urn: str, verdict: str) -> None:
        self.conn.execute(
            "UPDATE posts SET digest_verdict = ? WHERE urn = ?", (verdict, urn)
        )
        self.conn.commit()

    def kept_unnotified(self) -> list[sqlite3.Row]:
        """Kept posts we haven't emailed yet. Drives the notification step, so a
        run whose email failed retries on the next run instead of losing the hit."""
        return self.conn.execute(
            "SELECT * FROM posts "
            "WHERE digest_verdict = 'kept' AND notified_at IS NULL "
            "ORDER BY first_seen"
        ).fetchall()

    def mark_notified(self, urn: str) -> None:
        self.conn.execute(
            "UPDATE posts SET notified_at = ? WHERE urn = ?", (_now(), urn)
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
