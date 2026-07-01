#!/usr/bin/env bash
# Wrapper for scheduled (launchd) runs. Sources .env for SMTP credentials, then
# runs the collector unattended. Credentials live here (a gitignored .env) rather
# than in the plist, so they never show up in launchd's environment listing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Load SMTP creds: LJC_SMTP_USER, LJC_SMTP_PASS, LJC_EMAIL_TO, ...
if [ -f "$REPO/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO/.env"
  set +a
fi

# Prefer the project venv, fall back to system python3.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

exec "$PY" bot.py --unattended
