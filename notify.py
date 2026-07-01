"""Email notifications for new matches.

Two triggers:
  - notify_new_matches(): emails you the kept posts you haven't been emailed yet,
    then marks them notified so you're never pinged about the same post twice.
  - send_reauth_alert(): emails you when an unattended run hit the LinkedIn login
    wall and needs a human to sign in.

Config comes from env vars (see .env.example). Uses stdlib smtplib only, so a
Gmail address + app password is all you need — no API keys, no extra deps.
"""
from __future__ import annotations

import os
import smtplib
import sqlite3
from email.message import EmailMessage

from store import Store


def _config() -> dict | None:
    """Return SMTP config, or None (with a printed reason) if it's incomplete."""
    user = os.environ.get("LJC_SMTP_USER")
    password = os.environ.get("LJC_SMTP_PASS")
    if not user or not password:
        print("notify: LJC_SMTP_USER / LJC_SMTP_PASS unset — skipping email")
        return None
    return {
        "host": os.environ.get("LJC_SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("LJC_SMTP_PORT", "465")),
        "user": user,
        "password": password,
        "sender": os.environ.get("LJC_EMAIL_FROM", user),
        "to": os.environ.get("LJC_EMAIL_TO", user),
    }


def _send(subject: str, body: str) -> bool:
    cfg = _config()
    if not cfg:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["to"]
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    except Exception as exc:  # network, auth, etc. — don't crash the run
        print(f"notify: send failed ({type(exc).__name__}): {exc}")
        return False
    print(f"notify: emailed '{subject}' to {cfg['to']}")
    return True


def _format_post(r: sqlite3.Row) -> str:
    author = r["author"] or "unknown"
    headline = (r["headline"] or "").strip()
    text = (r["text"] or "").strip()
    if len(text) > 600:
        text = text[:600].rstrip() + "…"
    lines = [f"• {author}"]
    if headline:
        lines.append(f"  {headline}")
    if text:
        lines.append(f"  {text}")
    if r["url"]:
        lines.append(f"  {r['url']}")
    return "\n".join(lines)


def notify_new_matches(store: Store) -> int:
    """Email any kept-but-not-yet-notified posts. Returns how many were sent.

    Only marks a post notified if the email actually went out, so a failed send
    (bad creds, network) is retried on the next run rather than silently dropped.
    """
    rows = store.kept_unnotified()
    if not rows:
        return 0

    n = len(rows)
    subject = f"[LinkedIn] {n} new matching post{'s' if n != 1 else ''}"
    header = (
        f"{n} post{'s' if n != 1 else ''} matched your criteria. "
        "Open the post and DM the recruiter to get your foot in the door.\n\n"
    )
    body = header + ("\n\n".join(_format_post(r) for r in rows)) + "\n"

    if not _send(subject, body):
        print(f"notify: {n} match(es) pending — will retry next run")
        return 0

    for r in rows:
        store.mark_notified(r["urn"])
    return n


def send_reauth_alert() -> bool:
    """Tell the user an unattended run needs a manual LinkedIn login."""
    return _send(
        "[LinkedIn] Re-auth needed — collector hit the login wall",
        "A scheduled linkedin-job-collector run hit the LinkedIn login wall and "
        "exited without scraping.\n\n"
        "Run it once by hand and sign in to refresh the session:\n"
        "  cd linkedin-job-collector && python bot.py\n\n"
        "Scheduled runs will resume automatically once the session is live again.\n",
    )
