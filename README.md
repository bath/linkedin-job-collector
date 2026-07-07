# linkedin-job-collector

Personal LinkedIn job-search helper. It opens LinkedIn, saves matching content
search posts to SQLite, filters them with Claude or Cursor, and emails new
matches with a short hook plus applicant facts.

> This public repo is code only. Scraped posts, raw captures, `posts.db`,
> `.env`, and your LinkedIn browser profile stay local or in the private
> `data/` repo. Do not commit scraped LinkedIn data here.

## Getting Started

Already installed?

```sh
jobs update
jobs doctor
```

From a fresh checkout:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
git clone git@github.com:bath/linkedin-job-data.git data
cp .env.example .env
```

Fill in SMTP/email values in `.env`, then install the bare `jobs` command for
zsh:

```sh
./jobs install-shell
source ~/.zshrc
jobs doctor
```

Run it:

```sh
jobs
```

The first scrape opens Chromium. Log into LinkedIn manually; the session is kept
in `profile/`.

## Common Commands

```sh
jobs doctor                                      # validate local setup
jobs update                                      # install latest release bundle
jobs --query remote-swe --harness auto
jobs --query sf-swe --harness cursor
jobs --query san-francisco-swe --harness cursor
jobs --query sf-seed-swe --harness cursor
jobs --query sf-series-a-swe --harness cursor
jobs --custom-query "founding engineer remote" --harness cursor
jobs --query remote-swe --harness auto --dry-run --json
```

`jobs` is also a zsh builtin. `./jobs install-shell` adds a managed shell
function so bare `jobs` runs this collector. Use `builtin jobs` if you need the
zsh job-listing builtin.

## What It Does

- Scrapes LinkedIn content-search posts with a headed Chromium session.
- Stores deduped posts in `data/posts.db`.
- Filters posts with `auto`, `claude`, or `cursor` digest providers.
- Emails each kept post once.
- Saves raw captures under `data/artifacts/` for reparse/debugging.

## Queries And Harnesses

Prebuilt query IDs include:

```text
remote-swe
remote-platform
remote-data
sf-swe
san-francisco-swe
sf-seed-swe
sf-series-a-swe
```

Harness choices:

```text
auto    # try Claude, fall back to Cursor
claude  # force Claude Code
cursor  # force Cursor Agent / Composer 2.5
```

The default model settings live in `.env`:

```sh
LJC_DIGEST_PROVIDER=auto
LJC_CLAUDE_MODEL=haiku
LJC_CURSOR_MODEL=composer-2.5
```

Edit `prompts/filter.md` to change what counts as a match.

Quality smoke tests:

```sh
python scripts/smoke_digest_providers.py --provider cursor
python scripts/smoke_digest_providers.py --provider claude
```

## Email

Copy `.env.example` to `.env` and set:

```sh
LJC_SMTP_USER=
LJC_SMTP_PASS=
LJC_EMAIL_TO=
```

Gmail works with an app password. Other SMTP hosts can use the override values
in `.env.example`.

## Direct Bot Commands

Use these when you want to bypass the TUI:

```sh
python bot.py
python bot.py --no-digest
python bot.py --reparse data/artifacts/<ts>
```

After a scrape, commit the private data repo if you want to preserve the run:

```sh
cd data
git add -A
git commit -m "run <ts>"
git push
```

## Updating

`jobs update` downloads the latest GitHub Release, verifies the checksum when
available, and installs it over the current project files. It leaves gitignored
local state alone: `.env`, `.venv`, `data/`, and `profile/`.

Preview an update:

```sh
jobs update --dry-run --json
```

## Scheduling

Install the macOS LaunchAgent:

```sh
sed "s#__REPO__#$PWD#g" deploy/com.bath.linkedin-job-collector.plist \
  > ~/Library/LaunchAgents/com.bath.linkedin-job-collector.plist
launchctl load ~/Library/LaunchAgents/com.bath.linkedin-job-collector.plist
launchctl start com.bath.linkedin-job-collector
```

Logs go to `/tmp/linkedin-job-collector.*.log`. To stop it:

```sh
launchctl unload ~/Library/LaunchAgents/com.bath.linkedin-job-collector.plist
```

## Project Files

```text
bot.py             orchestration
extract.py         LinkedIn payload parsing
store.py           SQLite storage
digest.py          Claude/Cursor filtering
notify.py          email notifications
jobs               CLI/TUI entrypoint
jobs_cli.py        CLI/TUI implementation
prompts/filter.md  matching criteria
deploy/            LaunchAgent wrapper
```

## Debugging

If extraction breaks, reparse saved captures instead of re-scraping:

```sh
python bot.py --reparse data/artifacts/<ts>
```

The tool is intentionally conservative: headed browser, manual login, persistent
profile, jittered waits, and a small number of searches per run.
