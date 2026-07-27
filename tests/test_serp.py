import json
import re

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.credentials import registry
from evo_cli.credentials.store import compile_flat, set_value
from evo_cli.serp import client, creds, install, render
from evo_cli.serp.errors import SerpError

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text):
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", text))


PAYLOAD = {
    "search_metadata": {"total_time_taken": 1.23},
    "search_information": {"total_results": 4310000},
    "answer_box": {"type": "organic_result", "answer": "42"},
    "organic_results": [
        {
            "position": 1,
            "title": "Evo CLI on GitHub",
            "link": "https://github.com/maycuatroi/evo-cli",
            "snippet": "A developer toolbox [with brackets] in the snippet.",
            "displayed_link": "github.com",
        },
        {"position": 2, "title": "Evo CLI on PyPI", "link": "https://pypi.org/project/evo-cli/"},
    ],
    "related_questions": [{"question": "What is evo cli?"}],
}


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(creds.ENV_VAR, raising=False)


@pytest.fixture
def store(tmp_path, monkeypatch):
    omelet_dir = tmp_path / ".omelet.d"
    monkeypatch.setenv("OMELET_DIR", str(omelet_dir))
    monkeypatch.setenv("OMELET_CONFIG", str(tmp_path / ".omelet.json"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    (omelet_dir / "credentials").mkdir(parents=True)
    return omelet_dir / "credentials"


@pytest.fixture
def http_only(monkeypatch):
    """No serpapi binary in sight, so every command takes the HTTPS path."""
    monkeypatch.setattr(install, "find_binary", lambda: None)


def _fake_get_json(payload, calls=None):
    def _get_json(url, params, timeout=60):
        if calls is not None:
            calls.append((url, dict(params)))
        return payload

    return _get_json


# registration ---------------------------------------------------------------


def test_serp_commands_are_registered():
    assert "serp" in cli.commands
    assert set(cli.commands["serp"].commands) == {"search", "account", "locations", "raw", "doctor"}
    assert "serp" in cli.commands["setup"].commands


def test_serp_help_runs(runner):
    result = runner.invoke(cli, ["serp", "-h"])
    assert result.exit_code == 0
    assert "SerpApi" in _plain(result.output)


def test_setup_serp_help_runs(runner):
    result = runner.invoke(cli, ["setup", "serp", "-h"])
    assert result.exit_code == 0
    assert "--write-config" in _plain(result.output)


# credentials ----------------------------------------------------------------


def test_registry_knows_serpapi():
    spec = registry.spec_for_flat_key(creds.CRED_KEY)
    assert spec is not None
    assert spec["path"] == "tools/serpapi.json"


def test_key_resolution_prefers_env_then_store_then_config(store, monkeypatch):
    assert creds.resolve() == (None, None)

    creds.write_config_file("from-config")
    assert creds.resolve() == ("from-config", str(creds.config_file()))

    set_value(creds.CRED_KEY, "from-store")
    compile_flat()
    assert creds.resolve() == ("from-store", f"evo cred {creds.CRED_KEY}")

    monkeypatch.setenv(creds.ENV_VAR, "from-env")
    assert creds.resolve() == ("from-env", f"env {creds.ENV_VAR}")


def test_missing_key_explains_how_to_add_one(store):
    with pytest.raises(SerpError) as excinfo:
        creds.api_key()
    assert "evo setup serp" in str(excinfo.value)


def test_write_config_file_is_private(store):
    path = creds.write_config_file("secret-key")
    assert path.read_text(encoding="utf-8") == 'api_key = "secret-key"\n'
    assert path.stat().st_mode & 0o777 == 0o600


def test_mask_hides_the_middle():
    masked = creds.mask("0123456789abcdef0123456789abcdef")
    assert masked == "012345...cdef (32 chars)"
    assert "6789abcdef0123" not in masked


# params ---------------------------------------------------------------------


def test_build_params_defaults_to_google_web():
    params = client.build_params("coffee", num=5)
    assert params == {"engine": "google", "q": "coffee", "num": 5}


def test_build_params_sets_the_vertical_and_extras():
    params = client.build_params("openai", search_type="news", hl="vi", gl="vn", extra=("tbs=qdr:d",))
    assert params["tbm"] == "nws"
    assert params["hl"] == "vi"
    assert params["gl"] == "vn"
    assert params["tbs"] == "qdr:d"


def test_build_params_switches_engine_for_scholar():
    assert client.build_params("bert", search_type="scholar")["engine"] == "google_scholar"


def test_build_params_rejects_a_malformed_extra():
    with pytest.raises(SerpError):
        client.build_params("x", extra=("nope",))


def test_result_key_falls_back_to_what_the_payload_has():
    assert client.result_key_for("web", PAYLOAD) == "organic_results"
    assert client.result_key_for("images", {"organic_results": []}) == "organic_results"


def test_cli_argv_keeps_key_value_pairs():
    argv = client.cli_argv({"engine": "google", "q": "coffee shops"}, fields="organic_results")
    assert argv == ["search", "--fields", "organic_results", "engine=google", "q=coffee shops"]


def test_merge_pages_dedupes_by_link():
    first = {"organic_results": [{"link": "a"}, {"link": "b"}]}
    second = {"organic_results": [{"link": "b"}, {"link": "c"}]}
    merged = client.merge_pages([first, second], "organic_results")
    assert [item["link"] for item in merged["organic_results"]] == ["a", "b", "c"]


def test_api_errors_become_serp_errors(monkeypatch, store):
    monkeypatch.setattr(client, "get_json", lambda *a, **k: {"error": "Invalid API key"})
    with pytest.raises(SerpError, match="Invalid API key"):
        client.search_via_http({"q": "x"}, "bad-key")


def test_resolve_transport_demands_the_binary_for_cli_mode(http_only):
    assert client.resolve_transport("auto") == ("http", None)
    with pytest.raises(SerpError, match="evo setup serp"):
        client.resolve_transport("cli")


def test_search_paginates_with_start_offsets(monkeypatch, http_only):
    calls = []
    monkeypatch.setattr(client, "get_json", _fake_get_json(PAYLOAD, calls))
    payload, result_key, transport = client.search({"engine": "google", "q": "x", "num": 10}, pages=2, key="k")
    assert transport == "http"
    assert result_key == "organic_results"
    assert [call[1].get("start") for call in calls] == [None, 10]
    assert len(payload["organic_results"]) == 2  # both pages returned the same links


# rendering ------------------------------------------------------------------


def test_links_extracts_urls():
    assert render.links(PAYLOAD, "organic_results") == [
        "https://github.com/maycuatroi/evo-cli",
        "https://pypi.org/project/evo-cli/",
    ]


def test_item_helpers_cope_with_scholar_shaped_items():
    item = {"title": "Attention", "publication_info": {"summary": "Vaswani et al., 2017"}}
    assert render.item_title(item) == "Attention"
    assert render.item_snippet(item) == "Vaswani et al., 2017"


# search command -------------------------------------------------------------


def test_search_renders_results(runner, monkeypatch, store, http_only):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    monkeypatch.setattr(client, "get_json", _fake_get_json(PAYLOAD))
    result = runner.invoke(cli, ["serp", "search", "evo", "cli"])
    assert result.exit_code == 0
    output = _plain(result.output)
    assert "Evo CLI on GitHub" in output
    assert "https://github.com/maycuatroi/evo-cli" in output
    assert "[with brackets]" in output  # rich markup must not eat the snippet
    assert "2 results shown" in output
    assert "via http" in output


def test_search_sends_the_query_and_key(runner, monkeypatch, store, http_only):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    calls = []
    monkeypatch.setattr(client, "get_json", _fake_get_json(PAYLOAD, calls))
    result = runner.invoke(cli, ["serp", "search", "site:github.com serpapi", "-n", "5", "-l", "Austin, Texas"])
    assert result.exit_code == 0
    _, params = calls[0]
    assert params["q"] == "site:github.com serpapi"
    assert params["num"] == 5
    assert params["location"] == "Austin, Texas"
    assert params["api_key"] == "test-key"


def test_search_shows_at_most_the_requested_count(runner, monkeypatch, store, http_only):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    monkeypatch.setattr(client, "get_json", _fake_get_json(PAYLOAD))
    result = runner.invoke(cli, ["serp", "search", "evo", "-n", "1"])
    assert result.exit_code == 0
    output = _plain(result.output)
    assert "Evo CLI on GitHub" in output
    assert "Evo CLI on PyPI" not in output  # Google returns more than `num`; we do not print it
    assert "1 results shown" in output


def test_search_links_only_prints_bare_urls(runner, monkeypatch, store, http_only):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    monkeypatch.setattr(client, "get_json", _fake_get_json(PAYLOAD))
    result = runner.invoke(cli, ["serp", "search", "evo", "--links"])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "https://github.com/maycuatroi/evo-cli",
        "https://pypi.org/project/evo-cli/",
    ]


def test_search_json_is_machine_readable(runner, monkeypatch, store, http_only, tmp_path):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    monkeypatch.setattr(client, "get_json", _fake_get_json(PAYLOAD))
    out = tmp_path / "payload.json"
    result = runner.invoke(cli, ["serp", "search", "evo", "--json", "-o", str(out)])
    assert result.exit_code == 0
    assert json.loads(result.output)["organic_results"][0]["title"] == "Evo CLI on GitHub"
    assert json.loads(out.read_text(encoding="utf-8"))["organic_results"]


def test_search_without_a_key_points_at_setup(runner, store, http_only):
    result = runner.invoke(cli, ["serp", "search", "evo"])
    assert result.exit_code != 0
    assert "evo setup serp" in _plain(result.output)


def test_search_reports_an_empty_result_set(runner, monkeypatch, store, http_only):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    monkeypatch.setattr(client, "get_json", _fake_get_json({"organic_results": []}))
    result = runner.invoke(cli, ["serp", "search", "asdkjhasd"])
    assert result.exit_code == 0
    assert "no organic results" in _plain(result.output)


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_search_jq_hands_the_cli_output_over_untouched(runner, monkeypatch, store, tmp_path):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    monkeypatch.setattr(install, "find_binary", lambda: tmp_path / "serpapi")
    seen = {}

    def fake_run(binary, argv, key, timeout=90, capture=True):
        seen["argv"] = argv
        seen["key"] = key
        return _Completed(stdout='{"title":"SerpApi"}\n')

    monkeypatch.setattr(client, "run_binary", fake_run)
    result = runner.invoke(cli, ["serp", "search", "serpapi", "--jq", ".organic_results[0]"])
    assert result.exit_code == 0
    assert result.output == '{"title":"SerpApi"}\n'
    assert seen["argv"][:3] == ["search", "--jq", ".organic_results[0]"]
    assert seen["key"] == "test-key"


def test_raw_requires_the_binary_and_forwards_the_exit_code(runner, monkeypatch, store, http_only, tmp_path):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    result = runner.invoke(cli, ["serp", "raw", "search", "engine=google", "q=x"])
    assert result.exit_code != 0
    assert "evo setup serp" in _plain(result.output)

    monkeypatch.setattr(install, "find_binary", lambda: tmp_path / "serpapi")
    monkeypatch.setattr(client, "run_binary", lambda *a, **k: _Completed(returncode=2))
    result = runner.invoke(cli, ["serp", "raw", "search", "engine=google", "q=x"])
    assert result.exit_code == 2


# account / locations / doctor ------------------------------------------------


def test_account_shows_the_quota(runner, monkeypatch, store, http_only):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    monkeypatch.setattr(
        client, "get_json", _fake_get_json({"plan_name": "Free", "plan_searches_left": 97, "this_month_usage": 3})
    )
    result = runner.invoke(cli, ["serp", "account"])
    assert result.exit_code == 0
    output = _plain(result.output)
    assert "Free" in output
    assert "97" in output


def test_locations_lists_canonical_names(runner, monkeypatch, store):
    monkeypatch.setattr(
        client,
        "get_json",
        _fake_get_json([{"canonical_name": "Hanoi,Vietnam", "target_type": "Country", "reach": 1234}]),
    )
    result = runner.invoke(cli, ["serp", "locations", "Ha", "Noi"])
    assert result.exit_code == 0
    assert "Hanoi,Vietnam" in _plain(result.output)


def test_doctor_offline_reports_the_missing_pieces(runner, store, http_only):
    result = runner.invoke(cli, ["serp", "doctor", "--offline"])
    assert result.exit_code == 1
    output = _plain(result.output)
    assert "serpapi CLI" in output
    assert "evo setup serp" in output


def test_doctor_offline_passes_once_the_key_is_there(runner, monkeypatch, store, http_only):
    monkeypatch.setenv(creds.ENV_VAR, "test-key")
    result = runner.invoke(cli, ["serp", "doctor", "--offline"])
    assert result.exit_code == 0
    assert "test-key"[:6] not in _plain(result.output) or "..." in _plain(result.output)


# install --------------------------------------------------------------------


def test_asset_url_matches_the_release_naming(monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(install.platform, "machine", lambda: "arm64")
    assert install.asset_url("0.2.3").endswith("/v0.2.3/serpapi_0.2.3_darwin_arm64.tar.gz")


def test_platform_target_rejects_the_unsupported(monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Plan9")
    monkeypatch.setattr(install.platform, "machine", lambda: "risc")
    with pytest.raises(SerpError):
        install.platform_target()


def test_pick_method_prefers_brew_when_present(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None)
    assert install.pick_method("auto") == "brew"
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    assert install.pick_method("auto") == "binary"
    assert install.pick_method("go") == "go"


def _brew_runner(calls, untrusted=True):
    def run(args):
        calls.append(list(args))
        if args[0] == "install" and untrusted:
            return 1, "Error: Refusing to load formula serpapi/tap/serpapi-cli from untrusted tap serpapi/tap."
        return 0, ""

    return run


def test_install_brew_flags_an_untrusted_tap(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/brew")
    calls = []
    with pytest.raises(install.BrewTrustRequired, match="--trust-tap"):
        install.install_brew(runner=_brew_runner(calls))
    assert calls == [["tap", install.BREW_TAP], ["install", install.BREW_FORMULA]]


def test_install_brew_trusts_the_tap_when_asked(monkeypatch, tmp_path):
    monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(install, "find_binary", lambda: tmp_path / "serpapi")
    calls = []
    install.install_brew(trust=True, runner=_brew_runner(calls, untrusted=False))
    assert ["trust", "--formula", install.BREW_QUALIFIED] in calls


def test_setup_falls_back_to_the_release_binary_when_brew_is_untrusted(runner, monkeypatch, store, http_only, tmp_path):
    monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None)
    monkeypatch.setattr(install, "_brew", _brew_runner([]))
    monkeypatch.setattr(install, "install_binary", lambda version=None: (tmp_path / "serpapi", "0.2.3", "http://x"))
    monkeypatch.setattr(install, "binary_version", lambda binary: "serpapi 0.2.3")
    monkeypatch.setattr(client, "get_json", _fake_get_json({"plan_name": "Free"}))
    result = runner.invoke(cli, ["setup", "serp", "--api-key", "abc123"])
    assert result.exit_code == 0
    output = _plain(result.output)
    assert "falling back to the GitHub release binary" in output
    assert "serpapi 0.2.3" in output


def test_setup_with_explicit_brew_surfaces_the_trust_error(runner, monkeypatch, store, http_only):
    monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None)
    monkeypatch.setattr(install, "_brew", _brew_runner([]))
    result = runner.invoke(cli, ["setup", "serp", "--method", "brew", "--api-key", "abc123"])
    assert result.exit_code != 0
    assert "brew trust" in _plain(result.output)


def test_setup_serp_stores_the_key_and_verifies(runner, monkeypatch, store, http_only):
    monkeypatch.setattr(client, "get_json", _fake_get_json({"plan_name": "Free", "plan_searches_left": 100}))
    result = runner.invoke(cli, ["setup", "serp", "--method", "none", "--api-key", "abc123"], input="")
    assert result.exit_code == 0
    stored = json.loads((store / "tools" / "serpapi.json").read_text(encoding="utf-8"))
    assert stored["flat"][creds.CRED_KEY] == "abc123"
    assert stored["service"] == "SerpApi"
    output = _plain(result.output)
    assert "Free" in output
    assert "evo cred sync push" in output


def test_setup_serp_can_write_the_cli_config(runner, monkeypatch, store, http_only):
    monkeypatch.setattr(client, "get_json", _fake_get_json({"plan_name": "Free"}))
    result = runner.invoke(cli, ["setup", "serp", "--method", "none", "--api-key", "abc123", "--write-config"])
    assert result.exit_code == 0
    assert creds.config_file().read_text(encoding="utf-8") == 'api_key = "abc123"\n'
