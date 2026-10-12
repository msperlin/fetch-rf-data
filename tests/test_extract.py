import zipfile

import pytest

from rfbpy import extract


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_extract_archives(tmp_path):
    zip_dir = tmp_path / "zip"
    out_dir = tmp_path / "out"
    zip_dir.mkdir()
    make_zip(zip_dir / "a.zip", {"a.csv": "hello"})

    result = extract.extract_archives(zip_dir, out_dir)
    assert result.extracted == ["a.zip"]
    assert (out_dir / "a.csv").read_text() == "hello"
    assert result.ok


def test_extract_is_idempotent(tmp_path):
    zip_dir = tmp_path / "zip"
    out_dir = tmp_path / "out"
    zip_dir.mkdir()
    make_zip(zip_dir / "a.zip", {"a.csv": "hello"})

    extract.extract_archives(zip_dir, out_dir)
    result = extract.extract_archives(zip_dir, out_dir, skip_existing=True)
    assert result.extracted == []
    assert result.skipped == ["a.zip"]


def test_extract_replaces_changed_members(tmp_path):
    zip_dir = tmp_path / "zip"
    out_dir = tmp_path / "out"
    zip_dir.mkdir()
    make_zip(zip_dir / "a.zip", {"a.csv": "hello"})
    extract.extract_archives(zip_dir, out_dir)

    (out_dir / "a.csv").write_text("stale content")
    result = extract.extract_archives(zip_dir, out_dir, skip_existing=True)
    assert result.extracted == ["a.zip"]
    assert (out_dir / "a.csv").read_text() == "hello"


def test_remove_zip_after_extract(tmp_path):
    zip_dir = tmp_path / "zip"
    out_dir = tmp_path / "out"
    zip_dir.mkdir()
    make_zip(zip_dir / "a.zip", {"a.csv": "hello"})

    extract.extract_archives(zip_dir, out_dir, remove_zip_after_extract=True)
    assert not (zip_dir / "a.zip").exists()


def test_zip_slip_is_rejected(tmp_path):
    zip_dir = tmp_path / "zip"
    out_dir = tmp_path / "out"
    zip_dir.mkdir()
    make_zip(zip_dir / "evil.zip", {"../escape.csv": "boom"})

    result = extract.extract_archives(zip_dir, out_dir)
    assert result.failed == ["evil.zip"]
    assert not (tmp_path / "escape.csv").exists()


def test_no_zip_files(tmp_path):
    result = extract.extract_archives(tmp_path / "zip", tmp_path / "out")
    assert result.total == 0
