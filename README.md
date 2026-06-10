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

## Account safety

Runs against your real account, so it stays conservative: headed browser,
persistent profile, jittered waits, 1–2 searches per run, 1–2 runs/day. Selector
and Voyager-shape drift is expected — debug the parser against saved captures in
`data/artifacts/`, don't re-scrape to iterate.
