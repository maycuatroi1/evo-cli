import zipfile

import pytest

from evo_cli.commands import gdrive

FILE_ID = "1LxWckqs1zFIRzND5lSbPGJiz6-i3LIk_"

INTERSTITIAL = (
    '<html><body><form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
    '<input type="submit" id="uc-download-link" value="Download anyway"/>'
    f'<input type="hidden" name="id" value="{FILE_ID}">'
    '<input type="hidden" name="export" value="download">'
    '<input type="hidden" name="confirm" value="t">'
    '<input type="hidden" name="uuid" value="05f44d46-0e7b-40f8-abfe-6eb5dd97d4ed">'
    "</form></body></html>"
)


@pytest.mark.parametrize(
    "target",
    [
        f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing",
        f"https://drive.google.com/uc?export=download&id={FILE_ID}",
        f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download",
        FILE_ID,
    ],
)
def test_extract_file_id(target):
    assert gdrive.extract_file_id(target) == FILE_ID


def test_extract_file_id_rejects_junk():
    assert gdrive.extract_file_id("not-a-drive-link") is None


def test_parse_confirm_form():
    action, fields = gdrive.parse_confirm_form(INTERSTITIAL)
    assert action == "https://drive.usercontent.google.com/download"
    assert fields == {
        "id": FILE_ID,
        "export": "download",
        "confirm": "t",
        "uuid": "05f44d46-0e7b-40f8-abfe-6eb5dd97d4ed",
    }


def test_parse_confirm_form_without_form():
    assert gdrive.parse_confirm_form("<html><body>nope</body></html>") == (None, {})


@pytest.mark.parametrize(
    "name",
    ["__MACOSX/._a.png", "Delivery 2/__MACOSX/b.png", ".DS_Store", "Delivery 2/.DS_Store"],
)
def test_skip_archive_member(name):
    assert gdrive.skip_archive_member(name) is True


def test_keeps_real_members():
    assert gdrive.skip_archive_member("Delivery 2/theme 1/a.png") is False


def test_safe_member_path_rejects_traversal(tmp_path):
    assert gdrive.safe_member_path(tmp_path, "../escaped.png") is None
    assert gdrive.safe_member_path(tmp_path, "inside/ok.png") is not None


def test_extract_archive_skips_mac_noise(tmp_path):
    archive = tmp_path / "delivery.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Delivery 2/theme 1/a.png", b"png")
        bundle.writestr("Delivery 2/theme 1/b.png", b"png")
        bundle.writestr("Delivery 2/.DS_Store", b"junk")
        bundle.writestr("__MACOSX/Delivery 2/._a.png", b"junk")

    written = gdrive.extract_archive(archive, tmp_path)

    assert written == 2
    assert sorted(p.name for p in tmp_path.rglob("*.png")) == ["a.png", "b.png"]
    assert not (tmp_path / "__MACOSX").exists()
    assert not list(tmp_path.rglob(".DS_Store"))


def test_extract_archive_ignores_non_zip(tmp_path):
    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    assert gdrive.extract_archive(plain, tmp_path) == 0


def test_safe_download_name():
    assert gdrive.safe_download_name("Delivery 2.zip") == "Delivery 2.zip"
    assert gdrive.safe_download_name("bao cao thang 8.pdf") == "bao cao thang 8.pdf"
    assert gdrive.safe_download_name("../../etc/passwd") == "_.._etc_passwd"
    assert gdrive.safe_download_name("") == "download.bin"
