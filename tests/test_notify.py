import json

import notify
from store import Store


def test_format_post_uses_hook_and_facts(tmp_path):
    store = Store(tmp_path / "posts.db")
    store.upsert(
        {
            "urn": "urn:li:activity:keep",
            "author": "Cohere Health",
            "headline": "Hiring",
            "text": "Long scraped text that should not dominate the email.",
            "posted_at": None,
            "url": "https://example.com/job",
        },
        "test",
    )
    store.set_verdict("urn:li:activity:keep", "kept")
    store.set_digest_summary(
        "urn:li:activity:keep",
        "Lead with healthcare data-platform experience.",
        json.dumps(["Remote-first U.S. role.", "Stack includes Python and SQL."]),
    )

    row = store.kept_unnotified()[0]
    body = notify._format_post(row)

    assert "Hook: Lead with healthcare data-platform experience." in body
    assert "- Remote-first U.S. role." in body
    assert "Long scraped text" not in body
    assert "https://example.com/job" in body
