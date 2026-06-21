from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import plantuml


def test_plantuml_registered():
    assert "plantuml" in cli.commands


def test_plantuml_help_runs():
    result = CliRunner().invoke(cli, ["plantuml", "--help"])
    assert result.exit_code == 0
    for sub in ("install", "check", "render", "new", "themes"):
        assert sub in result.output


def test_pick_jar_asset_prefers_versioned_gpl_build():
    assets = [
        {"name": "plantuml-1.2026.6-javadoc.jar", "browser_download_url": "x"},
        {"name": "plantuml-1.2026.6-sources.jar", "browser_download_url": "x"},
        {"name": "plantuml-mit-1.2026.6.jar", "browser_download_url": "x"},
        {"name": "plantuml-1.2026.6.jar", "browser_download_url": "good"},
        {"name": "plantuml-1.2026.6.pdf", "browser_download_url": "x"},
    ]
    chosen = plantuml.pick_jar_asset(assets)
    assert chosen["name"] == "plantuml-1.2026.6.jar"


def test_resolve_download_fallback_on_failure(monkeypatch):
    def boom(_url):
        raise OSError("no network")

    monkeypatch.setattr(plantuml, "fetch_json", boom)
    url, ver = plantuml.resolve_download("latest")
    assert ver == plantuml.FALLBACK_VERSION
    assert url.endswith(f"plantuml-{plantuml.FALLBACK_VERSION}.jar")


def test_templates_are_well_formed():
    for kind, body in plantuml.TEMPLATES.items():
        assert body.lstrip().startswith("@start")
        assert "@end" in body


def test_new_creates_file(tmp_path):
    out = tmp_path / "demo.puml"
    result = CliRunner().invoke(cli, ["plantuml", "new", "sequence", "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("@startuml")


def test_new_rejects_existing_without_force(tmp_path):
    out = tmp_path / "demo.puml"
    out.write_text("keep", encoding="utf-8")
    result = CliRunner().invoke(cli, ["plantuml", "new", "class", "-o", str(out)])
    assert result.exit_code != 0
    assert out.read_text(encoding="utf-8") == "keep"


def test_output_for_maps_extension(tmp_path):
    src = tmp_path / "a.puml"
    src.write_text("@startuml\n@enduml\n", encoding="utf-8")
    assert plantuml.output_for(str(src), None, "svg").name == "a.svg"
    assert plantuml.output_for(str(src), None, "latex").name == "a.tex"


def test_build_config_smetana(monkeypatch):
    monkeypatch.setattr(plantuml, "find_dot", lambda: None)
    path = plantuml.build_config("cerulean", "auto")
    assert path is not None
    import os

    try:
        content = open(path, encoding="utf-8").read()
        assert "smetana" in content
        assert "!theme cerulean" in content
    finally:
        os.unlink(path)
