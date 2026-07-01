# linkedin-job-collector

Personal job-hunt tool: scrolls a LinkedIn **content search**, captures the
posts (via the page's internal Voyager JSON), dedupes them into SQLite, and
emits a Claude-filtered digest of genuine remote-SWE hiring posts.

> **This repo holds code only.** Scraped posts, the SQLite DB, and raw HTML/JSON
> captures live in a **separate private repo** cloned into `data/` (gitignored).
> Never commit scraped LinkedIn content here — it republishes other people's
> data and can leak your session tokens.

## Layout

```
linkedin-job-collector/        # this repo (public, code only)
├── bot.py            # orchestration: scroll, capture, store, digest
├── extract.py        # Voyager JSON -> posts (HTML fallback)
├── store.py          # SQLite (schema is the durable contract)
├── digest.py         # claude -p filter -> ranked digest.md
├── searches.yaml     # which searches to run + scroll limits
├── prompts/filter.md # the filter prompt
├── profile/          # persistent Chromium profile (gitignored — holds your session)
└── data/             # ← private repo cloned here (gitignored)
    ├── posts.db
    ├── artifacts/<ts>/*.html, *.voyager.json
    └── digest-<ts>.md
```

## Setup

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Clone your PRIVATE data repo into data/ (separate from this public repo):
git clone git@github.com:bath/linkedin-job-data.git data
```

## Run

```sh
python bot.py                 # run searches, store new posts, write a digest
python bot.py --no-digest     # scrape + store only
python bot.py --reparse data/artifacts/<ts>   # rebuild from saved captures, no scraping
```

First run opens a real Chromium window. Log into LinkedIn **by hand** — the bot
never automates login and will wait at the auth wall. Your session persists in
`profile/` for later runs.

Then commit the new data in the private repo:

```sh
cd data && git add -A && git commit -m "run <ts>" && git push
```

## Your criteria live in the filter prompt

What counts as a match is decided entirely by `prompts/filter.md` — the `claude -p`
filter reads it and returns which posts to keep. Tighten it to your exact "I'd DM
this recruiter" bar (role, seniority, comp signals, keywords, red flags). The
narrower the prompt, the fewer false-positive emails.

## Email notifications

When the filter keeps a post you haven't been emailed about, you get an email with
the author, headline, a text snippet, and the post URL — so you can open it and DM
the recruiter. Each kept post is emailed **once** (tracked by `notified_at` in the
DB); a run whose send fails retries on the next run.

One-time setup — Gmail app password (any SMTP host works via the overrides):

```sh
cp .env.example .env
# Create an app password: https://myaccount.google.com/apppasswords (needs 2FA)
# then fill in LJC_SMTP_USER / LJC_SMTP_PASS / LJC_EMAIL_TO in .env
```

`.env` is gitignored. `python bot.py` picks it up automatically if you `source` it,
but for manual runs you can also just `export` the vars. Scheduled runs load it via
`deploy/run.sh`.

## Scheduling (macOS, Mon/Wed/Fri)

Runs unattended on a schedule. If the LinkedIn session has expired, `--unattended`
doesn't hang at the login wall — it emails you a "re-auth needed" alert and exits;
run `python bot.py` by hand once to sign in, and scheduled runs resume.

```sh
# 1. point the job at this repo and install it
sed "s#__REPO__#$PWD#g" deploy/com.bath.linkedin-job-collector.plist \
  > ~/Library/LaunchAgents/com.bath.linkedin-job-collector.plist

# 2. load it (LaunchAgent = runs in your GUI session, so the headed browser opens)
launchctl load ~/Library/LaunchAgents/com.bath.linkedin-job-collector.plist

# run it once now to check wiring; logs -> /tmp/linkedin-job-collector.*.log
launchctl start com.bath.linkedin-job-collector

# change cadence: edit StartCalendarInterval in the plist (Weekday 1=Mon…5=Fri),
# then unload + reload. To stop scheduling:
launchctl unload ~/Library/LaunchAgents/com.bath.linkedin-job-collector.plist
```

## Account safety

Runs against your real account, so it stays conservative: headed browser,
persistent profile, jittered waits, 1–2 searches per run, 1–2 runs/day. Selector
and Voyager-shape drift is expected — debug the parser against saved captures in
`data/artifacts/`, don't re-scrape to iterate.
