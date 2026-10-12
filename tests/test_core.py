import io
import zipfile
from pathlib import Path

import pytest

from rfbpy import RFBData, webdav
from rfbpy.errors import ConfigError


def zip_with(name, data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(name, data)
    return buf.getvalue()


def file_entry(name, payload, url="http://x/", etag="e1"):
    return {
        "name": name,
        "url": url + name,
        "size": len(payload),
        "etag": etag,
        "last_modified": None,
    }


@pytest.fixture
def rfb(tmp_path):
    return RFBData(
        processing_dir=str(tmp_path),
        files_to_download=["Cnaes.zip"],
        show_progress=False,
    )


def test_missing_processing_dir_raises():
    with pytest.raises(ConfigError):
        RFBData(processing_dir="", files_to_download=["Cnaes.zip"])


def test_paths(rfb, tmp_path):
    assert rfb.zip_dir == tmp_path / "01_zip-files"
    assert rfb.unzip_dir == tmp_path / "02_unzipped_files"
    assert rfb.sqlite_dir == tmp_path / "03_sqlite_file"
    assert rfb.parquet_dir == tmp_path / "04_parquet_files"


def test_repr(rfb):
    assert "RFBData" in repr(rfb)


def test_list_months(rfb, monkeypatch):
    monkeypatch.setattr(webdav, "list_months", lambda *a, **k: ["2026-08", "2026-09"])
    assert rfb.list_months() == ["2026-08", "2026-09"]


def test_discover_returns_fileinfo(rfb, monkeypatch):
    payload = zip_with("Cnaes.CNAECSV", "0111301;X")
    monkeypatch.setattr(webdav, "get_all_links", lambda *a, **k: [file_entry("Cnaes.zip", payload)])
    files = rfb.discover()
    assert files[0].name == "Cnaes.zip"
    assert files[0].size == len(payload)


def test_check_and_download(rfb, monkeypatch, capsys):
    payload = b"data"
    monkeypatch.setattr(webdav, "get_all_links", lambda *a, **k: [file_entry("Cnaes.zip", payload)])

    report = rfb.check()
    printed = capsys.readouterr().out
    assert "RFB CNPJ" in printed
    assert "Cnaes.zip" in printed
    assert "To download   : 1" in printed
    assert "4 B" in printed
    assert [f.name for f in report.to_download] == ["Cnaes.zip"]
    assert report.needed_bytes == 4
    assert report.enough_disk

    def fake_download(url, dir_zip, request_timeout, **kwargs):
        (Path(dir_zip) / kwargs["name"]).write_bytes(payload)
        return True

    monkeypatch.setattr(webdav, "download_with_retries", fake_download)
    result = rfb.download()
    assert result.downloaded == ["Cnaes.zip"]
    assert result.ok

    report2 = rfb.check(print_report=False)
    assert report2.to_download == []
    assert [f.name for f in report2.up_to_date] == ["Cnaes.zip"]


def test_check_report_format(rfb, monkeypatch):
    payload = b"data"
    monkeypatch.setattr(webdav, "get_all_links", lambda *a, **k: [file_entry("Cnaes.zip", payload)])
    report = rfb.check(print_report=False)
    text = report.format()
    assert "Month      : latest" in text
    assert "Zip folder :" in text
    assert text == str(report)


def test_download_dry_run(rfb, monkeypatch):
    payload = b"data"
    monkeypatch.setattr(webdav, "get_all_links", lambda *a, **k: [file_entry("Cnaes.zip", payload)])
    result = rfb.download(dry_run=True)
    assert result.dry_run is True
    assert result.planned == ["Cnaes.zip"]
    assert not (rfb.zip_dir / "Cnaes.zip").exists()


def test_download_reports_failure(rfb, monkeypatch):
    payload = b"data"
    monkeypatch.setattr(webdav, "get_all_links", lambda *a, **k: [file_entry("Cnaes.zip", payload)])
    monkeypatch.setattr(webdav, "download_with_retries", lambda *a, **k: False)
    result = rfb.download()
    assert result.failed == ["Cnaes.zip"]
    assert not result.ok


def test_download_nothing_when_up_to_date(rfb, monkeypatch):
    payload = b"data"
    monkeypatch.setattr(webdav, "get_all_links", lambda *a, **k: [file_entry("Cnaes.zip", payload)])
    rfb.zip_dir.mkdir(parents=True, exist_ok=True)
    (rfb.zip_dir / "Cnaes.zip").write_bytes(payload)

    def boom(*a, **k):
        raise AssertionError("should not download")

    monkeypatch.setattr(webdav, "download_with_retries", boom)
    result = rfb.download()
    assert result.downloaded == []
    assert result.skipped == ["Cnaes.zip"]


def test_to_parquet_without_network(rfb):
    rfb.unzip_dir.mkdir(parents=True, exist_ok=True)
    (rfb.unzip_dir / "Cnaes.CNAECSV").write_bytes("0111301;X".encode("latin1"))
    result = rfb.to_parquet()
    assert result.destination == "parquet"
    assert result.tables["cnae"] == 1
    assert (rfb.parquet_dir / "cnae.parquet").exists()


def test_run_end_to_end_parquet(rfb, monkeypatch):
    payload = zip_with("Cnaes.CNAECSV", "0111301;Produção de arroz")
    monkeypatch.setattr(webdav, "get_all_links", lambda *a, **k: [file_entry("Cnaes.zip", payload)])

    def fake_download(url, dir_zip, request_timeout, **kwargs):
        (Path(dir_zip) / kwargs["name"]).write_bytes(payload)
        return True

    monkeypatch.setattr(webdav, "download_with_retries", fake_download)
    result = rfb.run(destination="parquet")
    assert result.ok
    assert result.load.tables["cnae"] == 1
    assert (rfb.parquet_dir / "cnae.parquet").exists()


def test_run_rejects_unknown_destination(rfb):
    with pytest.raises(ValueError):
        rfb.run(destination="csv")
