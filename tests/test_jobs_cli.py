import json
from pathlib import Path
import curses

import jobs_cli
from bot import selected_config


def test_build_selection_uses_prebuilt_query_and_harness():
    selection = jobs_cli.build_selection("remote-platform", "cursor", None, None)

    assert selection["query"]["id"] == "remote-platform"
    assert "platform" in selection["query"]["url"]
    assert selection["harness"]["id"] == "cursor"
    assert selection["harness"]["label"] == "Cursor (Composer 2.5)"
    assert selection["command"][-1] == "cursor"
    assert selection["command"][0].endswith("python")


def test_build_selection_supports_custom_query():
    selection = jobs_cli.build_selection(None, "auto", "founding engineer remote", None)

    assert selection["query"]["id"] == "custom-query"
    assert "founding%20engineer%20remote" in selection["query"]["url"]


def test_dry_run_json_output(capsys):
    rc = jobs_cli.main(["--query", "remote-swe", "--harness", "claude", "--dry-run", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["selection"]["query"]["id"] == "remote-swe"
    assert payload["selection"]["harness"]["id"] == "claude"
    assert payload["available"]["queries"]


def test_bot_selected_config_overrides_search_url():
    class Args:
        search_url = "https://www.linkedin.com/search/results/content/?keywords=test"
        search_name = "test-search"

    cfg = selected_config(Args())

    assert cfg["searches"] == [
        {
            "name": "test-search",
            "url": "https://www.linkedin.com/search/results/content/?keywords=test",
        }
    ]


def test_update_dry_run_json_output(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        jobs_cli,
        "build_update_plan",
        lambda repo, install_dir: jobs_cli.UpdatePlan(
            repo=repo,
            tag="main-1-abc1234",
            release_url="https://github.com/bath/linkedin-job-collector/releases/tag/main-1-abc1234",
            asset_name="linkedin-job-collector-main-1-abc1234.tar.gz",
            asset_url="https://example.com/archive.tar.gz",
            checksum_url="https://example.com/archive.tar.gz.sha256",
            install_dir=Path(install_dir).resolve(),
        ),
    )

    rc = jobs_cli.main(["update", "--install-dir", str(tmp_path), "--dry-run", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["tag"] == "main-1-abc1234"
    assert payload["checksum"] is True
    assert payload["install_dir"] == str(tmp_path.resolve())


def test_doctor_json_output(monkeypatch, capsys):
    monkeypatch.setattr(
        jobs_cli,
        "run_doctor_checks",
        lambda skip_network=False: [
            {"name": "required files", "status": "ok", "message": "present"},
            {"name": "network checks", "status": "warn", "message": "skipped"},
        ],
    )

    rc = jobs_cli.main(["doctor", "--skip-network", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["checks"][0]["name"] == "required files"


def test_doctor_fails_on_failed_check(monkeypatch, capsys):
    monkeypatch.setattr(
        jobs_cli,
        "run_doctor_checks",
        lambda skip_network=False: [
            {"name": "data repo", "status": "fail", "message": "missing data/"},
        ],
    )

    rc = jobs_cli.main(["doctor", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_install_shell_function_is_idempotent(tmp_path):
    rc = tmp_path / ".zshrc"
    jobs_path = tmp_path / "collector" / "jobs"
    jobs_path.parent.mkdir()
    jobs_path.write_text("#!/usr/bin/env python3\n")

    assert jobs_cli.install_shell_function(rc, jobs_path) is True
    first = rc.read_text()
    assert "linkedin-job-collector jobs" in first
    assert str(jobs_path.resolve()) in first
    assert "builtin `jobs`" in first

    assert jobs_cli.install_shell_function(rc, jobs_path) is False
    assert rc.read_text() == first


def test_install_shell_json_output(capsys, tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("# existing\n")

    rc_code = jobs_cli.main(["install-shell", "--shell-rc", str(rc), "--json"])

    assert rc_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "jobs"
    assert payload["shell_rc"] == str(rc)


def test_safe_curs_set_ignores_terminal_errors(monkeypatch):
    def raise_curses_error(_visibility):
        raise curses.error("unsupported")

    monkeypatch.setattr(jobs_cli.curses, "curs_set", raise_curses_error)

    jobs_cli.safe_curs_set(0)


def test_verify_checksum_rejects_mismatch(tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    checksum = tmp_path / "bundle.tar.gz.sha256"
    archive.write_bytes(b"actual")
    checksum.write_text("0" * 64 + "  bundle.tar.gz\n")

    try:
        jobs_cli._verify_checksum(archive, checksum)
    except jobs_cli.UpdateError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("expected checksum mismatch")
