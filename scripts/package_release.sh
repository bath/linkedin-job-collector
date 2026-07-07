#!/usr/bin/env bash
set -euo pipefail

out_dir="${1:-dist}"
version="${2:-dev}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
name="linkedin-job-collector-${version}"
work_dir="$(mktemp -d)"
bundle_dir="${work_dir}/${name}"

cleanup() {
  rm -rf "${work_dir}"
}
trap cleanup EXIT

mkdir -p "${bundle_dir}" "${out_dir}"

copy_path() {
  local path="$1"
  if [[ -e "${root}/${path}" ]]; then
    mkdir -p "${bundle_dir}/$(dirname "${path}")"
    cp -R "${root}/${path}" "${bundle_dir}/${path}"
  fi
}

for path in \
  README.md \
  .env.example \
  requirements.txt \
  requirements-dev.txt \
  jobs \
  jobs_cli.py \
  bot.py \
  digest.py \
  extract.py \
  notify.py \
  store.py \
  searches.yaml \
  prompts \
  deploy \
  scripts/package_release.sh \
  scripts/smoke_digest_providers.py
do
  copy_path "${path}"
done

cat > "${bundle_dir}/INSTALL.md" <<'EOF'
# Install

This release bundle contains the `jobs` executable plus the Python project files it
uses at runtime.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Clone the private data repository beside these files before real scraping:
git clone git@github.com:bath/linkedin-job-data.git data

./jobs --query remote-swe --harness auto --dry-run --json
./jobs
```

For bare `jobs` in zsh, add a shell function because `jobs` is a shell builtin:

```sh
jobs() {
  "/path/to/linkedin-job-collector/jobs" "$@"
}
```
EOF

printf '%s\n' "${version}" > "${bundle_dir}/VERSION"

tarball="${out_dir}/${name}.tar.gz"
tar -C "${work_dir}" -czf "${tarball}" "${name}"
if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "${tarball}" > "${tarball}.sha256"
else
  sha256sum "${tarball}" > "${tarball}.sha256"
fi

printf '%s\n' "${tarball}"
