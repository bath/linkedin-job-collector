# linkedin-job-collector

Personal job-hunt tool: scrolls a LinkedIn **content search**, captures the
posts (from the page's React Server Components / server-driven-UI payloads),
dedupes them into SQLite, emits a Claude-filtered digest of genuine remote-SWE
hiring posts, and emails you when new ones match.

> **This repo holds code only.** Scraped posts, the SQLite DB, and raw HTML/JSON
> captures live in a **separate private repo** cloned into `data/` (gitignored).
> Never commit scraped LinkedIn content here — it republishes other people's
> data and can leak your session tokens.

## Layout

```
linkedin-job-collector/        # this repo (public, code only)
├── bot.py            # orchestration: scroll, capture, store, digest, notify
├── extract.py        # RSC/SDUI payloads -> posts
├── store.py          # SQLite (schema is the durable contract)
├── digest.py         # Claude/Cursor filter -> ranked digest.md
├── notify.py         # email new matches / re-auth alerts (SMTP)
├── jobs              # guided TUI / binary-style entrypoint
├── jobs_cli.py       # TUI + noninteractive CLI implementation
├── searches.yaml     # which searches to run + scroll limits
├── prompts/filter.md # the filter prompt (your match criteria)
├── deploy/           # launchd job + run.sh wrapper for scheduling
├── profile/          # persistent Chromium profile (gitignored — holds your session)
└── data/             # ← private repo cloned here (gitignored)
    ├── posts.db
    ├── artifacts/<ts>/*.rsc-*.txt
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

## Releases

Every push to `main` runs tests and publishes a GitHub Release named
`main-<run-number>-<short-sha>`. The release contains:

- `linkedin-job-collector-<version>.tar.gz` — distributable bundle with `jobs`
  and the supporting Python project files
- `linkedin-job-collector-<version>.tar.gz.sha256` — checksum

Download the latest release:

```sh
gh release download --repo bath/linkedin-job-collector --pattern '*.tar.gz'
tar -xzf linkedin-job-collector-*.tar.gz
```

Then follow the bundle's `INSTALL.md` to create the venv, install dependencies,
clone the private `data/` repo, and run `./jobs`.

## Run

```sh
python bot.py                 # run searches, store new posts, write a digest
python bot.py --no-digest     # scrape + store only
python bot.py --reparse data/artifacts/<ts>   # rebuild from saved captures, no scraping
./jobs                        # guided TUI: pick query type + digest harness
```

First run opens a real Chromium window. Log into LinkedIn **by hand** — the bot
never automates login and will wait at the auth wall. Your session persists in
`profile/` for later runs.

For ad-hoc runs, `jobs` gives you an arrow-key selector for prebuilt searches,
custom searches, and the digest harness. It also has a noninteractive dry-run for
agents and scripts:

```sh
./jobs --query remote-swe --harness auto --dry-run --json
./jobs --custom-query "founding engineer remote" --harness cursor
./jobs --custom-url "https://www.linkedin.com/search/results/content/?..." --harness claude
```

Optional: install the bare `jobs` command in zsh. `jobs` is a shell builtin, so a
plain symlink on `$PATH` will usually not win command lookup. Install the managed
shell function instead:

```sh
./jobs install-shell
source ~/.zshrc
```

After this, `jobs` runs the collector. If you need zsh's original job-listing
builtin, run `builtin jobs`.

After that, run:

```sh
jobs
jobs doctor
jobs update
jobs --query remote-data --harness auto
jobs --custom-query "founding engineer remote" --harness cursor
jobs --query remote-swe --harness auto --dry-run --json
```

`jobs doctor` validates the local pipeline before you scrape: runtime files,
Python/venv dependencies, Playwright Chromium, private `data/`, `.env`,
LinkedIn `profile/`, command construction, latest GitHub Release reachability,
and Claude/Cursor harness availability. Agent-friendly forms:

```sh
jobs doctor --json
jobs doctor --skip-network --json
```

`jobs update` downloads the latest GitHub Release bundle, verifies the checksum
when the `.sha256` asset is present, and installs it over the current project
files. It does not remove local files that are not in the bundle, so your
gitignored `.env`, `data/`, `profile/`, and venv stay local. Preview an update:

```sh
jobs update --dry-run --json
```

Then commit the new data in the private repo:

```sh
cd data && git add -A && git commit -m "run <ts>" && git push
```

## Your criteria live in the filter prompt

What counts as a match is decided entirely by `prompts/filter.md` — the digest
provider reads it and returns which posts to keep. Tighten it to your exact "I'd
DM this recruiter" bar (role, seniority, comp signals, keywords, red flags). The
narrower the prompt, the fewer false-positive emails.

`digest.py` supports three provider modes:

```sh
LJC_DIGEST_PROVIDER=auto    # default: try Claude, then Cursor if Claude fails
LJC_DIGEST_PROVIDER=claude  # force Claude Code
LJC_DIGEST_PROVIDER=cursor  # force Cursor Agent
```

Default models are intentionally the minimum expected to work well for this
classification task: `LJC_CLAUDE_MODEL=haiku` and
`LJC_CURSOR_MODEL=composer-2.5`. Cursor Composer 2.5 is the default Cursor
harness option. Before trusting a lower-cost model, run the synthetic quality
smoke:

```sh
python scripts/smoke_digest_providers.py --provider claude
python scripts/smoke_digest_providers.py --provider cursor
```

The smoke must keep concrete remote SWE hiring posts and drop non-SWE/non-remote
or vendor-marketing posts. If a model fails that bar, override it in `.env`.

## Email notifications

When the filter keeps a post you haven't been emailed about, you get an email with
a bare-bones application hook, 5-10 applicant facts, and the post URL — so you can
quickly decide whether to apply or DM the recruiter. Each kept post is emailed
**once** (tracked by `notified_at` in the DB); a run whose send fails retries on
the next run.

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
persistent profile, jittered waits, 1–2 searches per run, a few runs/week.
RSC/SDUI payload drift is expected — when extraction breaks, debug `extract.py`
against the raw captures saved in `data/artifacts/<ts>/*.rsc-*.txt`
(`python bot.py --reparse data/artifacts/<ts>`); don't re-scrape to iterate.
