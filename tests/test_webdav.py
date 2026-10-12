import io
import zipfile
from pathlib import Path

import pytest
import requests

from rfbpy import webdav
from rfbpy.errors import ShareAccessError


def entry(href, is_collection=False, size=None, mtime=None, etag=None):
    return {
        "href": href,
        "is_collection": is_collection,
        "size": size,
        "last_modified": mtime,
        "etag": etag,
    }


def zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("x.txt", "hello")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, chunks, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, stream=True, timeout=None, headers=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return self.response


class FakePropfindResponse:
    def __init__(self, status, content):
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakePropfindSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def request(self, *args, **kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


MULTISTATUS = (
    b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
    b"<d:response><d:href>/x/2026-09/</d:href><d:propstat><d:prop>"
    b"<d:collection/><d:getetag>&quot;abc&quot;</d:getetag>"
    b"</d:prop></d:propstat></d:response></d:multistatus>"
)


def test_404_becomes_share_access_error(monkeypatch):
    class FakeHTTPResponse:
        status_code = 404

    class FakeReqSession:
        def request(self, *args, **kwargs):
            raise requests.HTTPError(response=FakeHTTPResponse())

    monkeypatch.setattr(webdav, "_get_session", lambda *a, **k: FakeReqSession())
    with pytest.raises(ShareAccessError):
        webdav.list_months("http://host/x/", 5)


def test_empty_200_is_retried_until_multistatus():
    session = FakePropfindSession(
        [FakePropfindResponse(200, b""), FakePropfindResponse(207, MULTISTATUS)]
    )
    entries = webdav._propfind(session, "http://host/x/", 5, attempts=3, retry_delay=0)
    assert session.calls == 2
    assert entries[0]["href"] == "/x/2026-09/"
    assert entries[0]["is_collection"] is True
    assert entries[0]["etag"] == "abc"


def test_all_empty_200_raises():
    session = FakePropfindSession([FakePropfindResponse(200, b"")])
    with pytest.raises(ShareAccessError):
        webdav._propfind(session, "http://host/x/", 5, attempts=3, retry_delay=0)
    assert session.calls == 3


def test_connection_reset_is_retried():
    session = FakePropfindSession(
        [requests.ConnectionError("reset"), FakePropfindResponse(207, MULTISTATUS)]
    )
    entries = webdav._propfind(session, "http://host/x/", 5, attempts=3, retry_delay=0)
    assert session.calls == 2
    assert len(entries) == 1


BASE = "http://host/public.php/webdav/Dados/Cadastros/CNPJ/"


def test_latest_is_used_filtered_and_sorted(monkeypatch):
    folders = [
        entry(BASE, is_collection=True),
        entry(BASE + "2026-08/", is_collection=True),
        entry(BASE + "2026-09/", is_collection=True),
    ]
    files = [
        entry(BASE + "2026-09/Simples.zip", size=300),
        entry(BASE + "2026-09/Cnaes.zip", size=20),
        entry(BASE + "2026-09/Empresas0.zip", size=100),
        entry(BASE + "2026-09/layout.pdf", size=1),
        entry(BASE + "2026-09/subdir", is_collection=True),
    ]

    def fake_propfind(session, url, timeout):
        return files if url.endswith("2026-09/") else folders

    monkeypatch.setattr(webdav, "_propfind", fake_propfind)
    result = webdav.get_all_links(BASE, ["Cnaes", "Empresas"], 5)

    assert [f["name"] for f in result] == ["Cnaes.zip", "Empresas0.zip"]
    assert all("2026-09" in f["url"] for f in result)


def test_specific_month(monkeypatch):
    folders = [entry(BASE + "2026-09/", is_collection=True)]
    files = [entry(BASE + "2026-08/Cnaes.zip", size=20)]

    def fake_propfind(session, url, timeout):
        return files if url.endswith("2026-08/") else folders

    monkeypatch.setattr(webdav, "_propfind", fake_propfind)
    result = webdav.get_all_links(BASE, ["Cnaes"], 5, month="2026-08")
    assert len(result) == 1
    assert "2026-08" in result[0]["url"]


def test_list_months_sorted(monkeypatch):
    folders = [
        entry(BASE + "2026-09/", is_collection=True),
        entry(BASE + "2026-07/", is_collection=True),
        entry(BASE + "not-a-month/", is_collection=True),
    ]
    monkeypatch.setattr(webdav, "_propfind", lambda *a, **k: folders)
    assert webdav.list_months(BASE, 5) == ["2026-07", "2026-09"]


def _run_download(monkeypatch, fake, tmp_path, url="http://host/a.zip", **kwargs):
    kwargs.setdefault("show_progress_bar", False)
    monkeypatch.setattr(webdav, "_get_session", lambda *a, **k: fake)
    return webdav.download_with_retries(url, tmp_path, 5, **kwargs)


def test_download_success_writes_and_verifies(monkeypatch, tmp_path):
    data = zip_bytes()
    fake = FakeSession(FakeResponse([data], headers={"content-length": str(len(data))}))
    assert _run_download(monkeypatch, fake, tmp_path, expected_size=len(data))
    assert (tmp_path / "a.zip").read_bytes() == data
    assert not (tmp_path / "a.zip.part").exists()


def test_download_size_mismatch_fails(monkeypatch, tmp_path):
    fake = FakeSession(FakeResponse([b"xx"], headers={"content-length": "2"}))
    assert not _run_download(monkeypatch, fake, tmp_path, expected_size=10, attempts=1)
    assert not (tmp_path / "a.zip").exists()


def test_download_invalid_zip_fails(monkeypatch, tmp_path):
    data = b"this is definitely not a zip"
    fake = FakeSession(FakeResponse([data], headers={"content-length": str(len(data))}))
    assert not _run_download(monkeypatch, fake, tmp_path, attempts=1, verify_zip=True)


def test_download_resume_uses_range_and_appends(monkeypatch, tmp_path):
    data = zip_bytes()
    half = len(data) // 2
    (tmp_path / "a.zip.part").write_bytes(data[:half])
    fake = FakeSession(
        FakeResponse(
            [data[half:]], status=206, headers={"content-length": str(len(data) - half)}
        )
    )
    assert _run_download(monkeypatch, fake, tmp_path, expected_size=len(data))
    assert fake.calls[0]["headers"].get("Range") == f"bytes={half}-"
    assert (tmp_path / "a.zip").read_bytes() == data


def test_download_resume_complete_promotes_without_request(monkeypatch, tmp_path):
    data = zip_bytes()
    (tmp_path / "a.zip.part").write_bytes(data)
    fake = FakeSession(FakeResponse([]))
    assert _run_download(monkeypatch, fake, tmp_path, expected_size=len(data))
    assert fake.calls == []
    assert (tmp_path / "a.zip").read_bytes() == data


def test_download_range_ignored_restarts(monkeypatch, tmp_path):
    data = zip_bytes()
    (tmp_path / "a.zip.part").write_bytes(b"stale-partial")
    fake = FakeSession(
        FakeResponse([data], status=200, headers={"content-length": str(len(data))})
    )
    assert _run_download(monkeypatch, fake, tmp_path, expected_size=len(data))
    assert (tmp_path / "a.zip").read_bytes() == data
