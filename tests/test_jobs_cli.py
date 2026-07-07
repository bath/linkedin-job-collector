import json

import jobs_cli
from bot import selected_config


def test_build_selection_uses_prebuilt_query_and_harness():
    selection = jobs_cli.build_selection("remote-platform", "cursor", None, None)

    assert selection["query"]["id"] == "remote-platform"
    assert "platform" in selection["query"]["url"]
    assert selection["harness"]["id"] == "cursor"
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
