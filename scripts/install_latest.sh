#!/usr/bin/env bash
set -euo pipefail

repo="${LJC_REPO:-bath/linkedin-job-collector}"
install_dir="${LJC_INSTALL_DIR:-${HOME}/repos/linkedin-job-collector}"
data_repo="${LJC_DATA_REPO:-git@github.com:bath/linkedin-job-data.git}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'install: missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

need curl
need python3
need tar

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

release_json="${tmp_dir}/release.json"
curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" -o "${release_json}"

read -r tag tarball_url checksum_url < <(
  python3 - "${release_json}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    release = json.load(fh)

tag = release["tag_name"]
tarball_url = ""
checksum_url = ""
for asset in release.get("assets", []):
    name = asset.get("name", "")
    url = asset.get("browser_download_url", "")
    if name.endswith(".tar.gz") and not tarball_url:
        tarball_url = url
    elif name.endswith(".tar.gz.sha256") and not checksum_url:
        checksum_url = url

if not tarball_url:
    raise SystemExit("latest release has no .tar.gz asset")

print(tag, tarball_url, checksum_url)
PY
)

archive="${tmp_dir}/bundle.tar.gz"
checksum="${tmp_dir}/bundle.tar.gz.sha256"
curl -fsSL "${tarball_url}" -o "${archive}"

if [[ -n "${checksum_url}" ]]; then
  curl -fsSL "${checksum_url}" -o "${checksum}"
  python3 - "${archive}" "${checksum}" <<'PY'
import hashlib
import sys

archive_path, checksum_path = sys.argv[1:]
expected = open(checksum_path, encoding="utf-8").read().split()[0]
digest = hashlib.sha256()
with open(archive_path, "rb") as fh:
    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
        digest.update(chunk)
actual = digest.hexdigest()
if actual != expected:
    raise SystemExit(f"checksum mismatch: expected {expected}, got {actual}")
print(f"{archive_path}: OK")
PY
fi

extract_dir="${tmp_dir}/extract"
mkdir -p "${extract_dir}" "${install_dir}"
tar -xzf "${archive}" -C "${extract_dir}"
bundle_dir="$(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "${bundle_dir}" ]]; then
  printf 'install: release archive did not contain a bundle directory\n' >&2
  exit 1
fi

cp -R "${bundle_dir}/." "${install_dir}/"
chmod +x "${install_dir}/jobs" "${install_dir}/scripts/"*.sh 2>/dev/null || true

cd "${install_dir}"
python3 -m venv .venv
"${install_dir}/.venv/bin/python" -m pip install --upgrade pip
"${install_dir}/.venv/bin/pip" install -r requirements.txt
"${install_dir}/.venv/bin/python" -m playwright install chromium

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
fi

if [[ ! -d data && "${LJC_SKIP_DATA_CLONE:-0}" != "1" ]]; then
  if command -v git >/dev/null 2>&1; then
    git clone "${data_repo}" data || {
      printf 'install: warning: could not clone private data repo; run this later:\n' >&2
      printf '  git clone %s %s/data\n' "${data_repo}" "${install_dir}" >&2
    }
  else
    printf 'install: warning: git not found; clone %s into %s/data later\n' "${data_repo}" "${install_dir}" >&2
  fi
fi

"${install_dir}/jobs" install-shell

printf '\nInstalled linkedin-job-collector %s into %s\n' "${tag}" "${install_dir}"
printf 'Next:\n'
printf '  1. Fill in %s/.env\n' "${install_dir}"
printf '  2. Run: source ~/.zshrc\n'
printf '  3. Run: jobs doctor\n'
printf '  4. Run: jobs\n'
