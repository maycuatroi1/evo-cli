import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from evo_cli import console, imaging
from evo_cli.imaging import ncnn
from evo_cli.imaging.errors import ImagingError

PINNED_WINDOWS_ASSET = (
    "https://github.com/upscayl/upscayl-ncnn/releases/download/"
    "20251207-174704/upscayl-bin-20251207-174704-windows.zip"
)


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("the test tried to reach the network")

    def no_binary(*args, **kwargs):
        pytest.fail("the test tried to spawn upscayl-bin")

    monkeypatch.setenv("EVO_UPSCAYL_DIR", str(tmp_path / "upscayl"))
    monkeypatch.setattr(ncnn.shutil, "which", lambda name: None)
    monkeypatch.setattr(console, "download_file", no_network)
    monkeypatch.setattr(ncnn, "_run", no_binary)
    return tmp_path / "upscayl"


@pytest.fixture
def installed(sandbox):
    binary = ncnn.binary_dir() / f"upscayl-bin-{ncnn.RELEASE}-windows" / ncnn.BINARY_NAME
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("binary", encoding="utf-8")
    models = ncnn.models_dir()
    models.mkdir(parents=True, exist_ok=True)
    for ext in ncnn.MODEL_EXTS:
        (models / f"{ncnn.DEFAULT_MODEL}.{ext}").write_text("weights", encoding="utf-8")
    return binary


def _write_release_zip(destination, member=None):
    member = member or f"upscayl-bin-{ncnn.RELEASE}-windows/{ncnn.BINARY_NAME}"
    with zipfile.ZipFile(destination, "w") as bundle:
        bundle.writestr(member, "binary")


def _zip_downloader(calls, member=None):
    def download(url, destination, description=""):
        calls.append(url)
        _write_release_zip(destination, member)

    return download


def _file_downloader(calls, body="weights"):
    def download(url, destination, description=""):
        calls.append(url)
        Path(destination).write_text(body, encoding="utf-8")

    return download


def _runner(calls, returncode=0, produce=True):
    def run(cmd):
        calls.append(cmd)
        if produce:
            Path(cmd[cmd.index("-o") + 1]).write_text("png", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, "", "upscayl-bin: something went wrong")

    return run


def test_ncnn_asset_url_pins_the_release():
    assert ncnn.asset_url("windows") == PINNED_WINDOWS_ASSET
    assert ncnn.RELEASE in ncnn.asset_url("linux")
    assert "latest" not in ncnn.asset_url("macos")


@pytest.mark.parametrize("system,slug", [("Windows", "windows"), ("Darwin", "macos"), ("Linux", "linux")])
def test_ncnn_asset_url_maps_the_platform_name(monkeypatch, system, slug):
    monkeypatch.setattr(ncnn.platform, "system", lambda: system)
    assert ncnn.asset_url().endswith(f"-{slug}.zip")


def test_ncnn_asset_url_rejects_an_unknown_platform(monkeypatch):
    monkeypatch.setattr(ncnn.platform, "system", lambda: "Haiku")
    with pytest.raises(ImagingError) as excinfo:
        ncnn.asset_url()
    assert "haiku" in str(excinfo.value).lower()


def test_ncnn_model_urls_pair_the_bin_and_the_param():
    urls = ncnn.model_urls("ultrasharp-4x")
    assert urls == (
        "https://raw.githubusercontent.com/upscayl/upscayl/main/resources/models/ultrasharp-4x.bin",
        "https://raw.githubusercontent.com/upscayl/upscayl/main/resources/models/ultrasharp-4x.param",
    )


def test_ncnn_find_binary_prefers_path(monkeypatch, tmp_path):
    on_path = tmp_path / "bin" / ncnn.BINARY_NAME
    on_path.parent.mkdir(parents=True)
    on_path.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(ncnn.shutil, "which", lambda name: str(on_path) if name == "upscayl-bin" else None)
    assert ncnn.find_binary() == on_path


def test_ncnn_find_binary_falls_back_to_the_cache_dir(installed):
    assert ncnn.find_binary() == installed


def test_ncnn_find_binary_is_none_when_nothing_is_installed():
    assert ncnn.find_binary() is None


def test_ncnn_ensure_binary_downloads_and_unpacks_the_pinned_zip():
    calls = []
    binary = ncnn.ensure_binary(_zip_downloader(calls))
    assert calls == [ncnn.asset_url()]
    assert binary.is_file()
    assert binary.name == ncnn.BINARY_NAME
    assert ncnn.binary_dir() in binary.parents


def test_ncnn_ensure_binary_reuses_what_is_already_unpacked(installed):
    def refuse(*args, **kwargs):
        pytest.fail("ensure_binary downloaded again instead of reusing the cache")

    assert ncnn.ensure_binary(refuse) == installed


def test_ncnn_ensure_binary_reports_a_zip_without_the_binary():
    calls = []
    with pytest.raises(ImagingError) as excinfo:
        ncnn.ensure_binary(_zip_downloader(calls, member="upscayl-bin-windows/README.md"))
    assert ncnn.BINARY_NAME in str(excinfo.value)


def test_ncnn_ensure_model_downloads_the_pair_once():
    calls = []
    models = ncnn.ensure_model("remacri-4x", _file_downloader(calls))
    assert calls == list(ncnn.model_urls("remacri-4x"))
    assert (models / "remacri-4x.bin").is_file()
    assert (models / "remacri-4x.param").is_file()

    ncnn.ensure_model("remacri-4x", _file_downloader(calls))
    assert len(calls) == 2


def test_ncnn_ensure_model_rejects_an_unknown_name():
    with pytest.raises(ImagingError) as excinfo:
        ncnn.ensure_model("not-a-model", _file_downloader([]))
    assert ncnn.DEFAULT_MODEL in str(excinfo.value)


def test_ncnn_command_passes_resize_without_scale(tmp_path):
    cmd = ncnn.build_command("upscayl-bin", tmp_path / "a.png", tmp_path / "b.png", "ultrasharp-4x", (1200, 800), "m")
    assert cmd[cmd.index("-r") + 1] == "1200x800"
    assert "-s" not in cmd
    assert cmd[cmd.index("-n") + 1] == "ultrasharp-4x"
    assert cmd[cmd.index("-f") + 1] == "png"


def test_ncnn_command_without_a_size_has_no_resize_flag(tmp_path):
    cmd = ncnn.build_command("upscayl-bin", tmp_path / "a.png", tmp_path / "b.png", "ultrasharp-4x", None, "m")
    assert "-r" not in cmd
    assert "-s" not in cmd


def test_ncnn_upscale_runs_the_binary_and_returns_the_output(installed, tmp_path):
    src = tmp_path / "in.png"
    src.write_text("png", encoding="utf-8")
    dst = tmp_path / "out" / "in.png"
    calls = []
    result = ncnn.upscale(src, dst, size=(64, 32), runner=_runner(calls))
    assert result == dst
    assert dst.is_file()
    assert calls[0][0] == str(installed)
    assert calls[0][calls[0].index("-r") + 1] == "64x32"
    assert calls[0][calls[0].index("-m") + 1] == str(ncnn.models_dir())


def test_ncnn_upscale_raises_when_the_binary_fails(installed, tmp_path):
    src = tmp_path / "in.png"
    src.write_text("png", encoding="utf-8")
    with pytest.raises(ImagingError) as excinfo:
        ncnn.upscale(src, tmp_path / "out.png", runner=_runner([], returncode=1, produce=False))
    assert "something went wrong" in str(excinfo.value)


def test_ncnn_module_never_imports_pillow_or_numpy():
    source = Path(ncnn.__file__).read_text(encoding="utf-8")
    assert "PIL" not in source
    assert "numpy" not in source


def test_ncnn_siblings_hint_the_image_extra_when_pillow_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "numpy", None)
    for loader in (imaging.load_pillow, imaging.load_numpy):
        with pytest.raises(ImagingError) as excinfo:
            loader()
        assert "pip install evo_cli[image]" in str(excinfo.value)
