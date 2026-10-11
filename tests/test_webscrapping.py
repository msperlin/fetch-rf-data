import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import requests

from fcts.webscrapping import (
    ShareAccessError,
    _propfind,
    download_with_retries,
    get_all_links,
    list_months,
)


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


class TestPropfindErrors(unittest.TestCase):
    def test_404_becomes_share_access_error(self):
        class FakeHTTPResponse:
            status_code = 404

        class FakeReqSession:
            def request(self, *args, **kwargs):
                raise requests.HTTPError(response=FakeHTTPResponse())

        with mock.patch("fcts.webscrapping._get_session", return_value=FakeReqSession()):
            with self.assertRaises(ShareAccessError):
                list_months("http://host/x/", 5)

    def test_empty_200_is_retried_until_multistatus(self):
        session = FakePropfindSession(
            [FakePropfindResponse(200, b""), FakePropfindResponse(207, MULTISTATUS)]
        )
        entries = _propfind(session, "http://host/x/", 5, attempts=3, retry_delay=0)
        self.assertEqual(session.calls, 2)
        self.assertEqual(entries[0]["href"], "/x/2026-09/")
        self.assertTrue(entries[0]["is_collection"])
        self.assertEqual(entries[0]["etag"], "abc")

    def test_all_empty_200_raises(self):
        session = FakePropfindSession([FakePropfindResponse(200, b"")])
        with self.assertRaises(ShareAccessError):
            _propfind(session, "http://host/x/", 5, attempts=3, retry_delay=0)
        self.assertEqual(session.calls, 3)

    def test_connection_reset_is_retried(self):
        session = FakePropfindSession(
            [requests.ConnectionError("reset"), FakePropfindResponse(207, MULTISTATUS)]
        )
        entries = _propfind(session, "http://host/x/", 5, attempts=3, retry_delay=0)
        self.assertEqual(session.calls, 2)
        self.assertEqual(len(entries), 1)


class TestDiscovery(unittest.TestCase):
    BASE = "http://host/public.php/webdav/Dados/Cadastros/CNPJ/"

    def _fake_propfind(self, folder_entries, file_entries):
        def _propfind(session, url, timeout):
            if url.endswith("2026-09/"):
                return file_entries
            return folder_entries

        return _propfind

    def test_latest_is_used_and_filtered_and_sorted(self):
        folders = [
            entry(self.BASE, is_collection=True),
            entry(self.BASE + "2026-08/", is_collection=True),
            entry(self.BASE + "2026-09/", is_collection=True),
        ]
        files = [
            entry(self.BASE + "2026-09/Simples.zip", size=300),
            entry(self.BASE + "2026-09/Cnaes.zip", size=20),
            entry(self.BASE + "2026-09/Empresas0.zip", size=100),
            entry(self.BASE + "2026-09/layout.pdf", size=1),
            entry(self.BASE + "2026-09/subdir", is_collection=True),
        ]
        with mock.patch(
            "fcts.webscrapping._propfind", side_effect=self._fake_propfind(folders, files)
        ):
            result = get_all_links(self.BASE, ["Cnaes", "Empresas"], 5)

        self.assertEqual([f["name"] for f in result], ["Cnaes.zip", "Empresas0.zip"])
        self.assertTrue(all("2026-09" in f["url"] for f in result))

    def test_specific_month(self):
        folders = [entry(self.BASE + "2026-09/", is_collection=True)]
        files = [entry(self.BASE + "2026-08/Cnaes.zip", size=20)]

        def fake_propfind(session, url, timeout):
            if url.endswith("2026-08/"):
                return files
            return folders

        with mock.patch("fcts.webscrapping._propfind", side_effect=fake_propfind):
            result = get_all_links(self.BASE, ["Cnaes"], 5, month="2026-08")

        self.assertEqual(len(result), 1)
        self.assertIn("2026-08", result[0]["url"])

    def test_list_months_sorted(self):
        folders = [
            entry(self.BASE + "2026-09/", is_collection=True),
            entry(self.BASE + "2026-07/", is_collection=True),
            entry(self.BASE + "not-a-month/", is_collection=True),
        ]
        with mock.patch("fcts.webscrapping._propfind", return_value=folders):
            self.assertEqual(list_months(self.BASE, 5), ["2026-07", "2026-09"])


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, fake, url="http://host/a.zip", **kwargs):
        kwargs.setdefault("show_progress_bar", False)
        with mock.patch("fcts.webscrapping._get_session", return_value=fake):
            return download_with_retries(url, self.dir, 5, **kwargs)

    def test_success_writes_and_verifies(self):
        data = zip_bytes()
        fake = FakeSession(FakeResponse([data], headers={"content-length": str(len(data))}))
        self.assertTrue(self._run(fake, expected_size=len(data)))
        self.assertEqual((self.dir / "a.zip").read_bytes(), data)
        self.assertFalse((self.dir / "a.zip.part").exists())

    def test_size_mismatch_fails_and_keeps_no_target(self):
        fake = FakeSession(FakeResponse([b"xx"], headers={"content-length": "2"}))
        self.assertFalse(self._run(fake, expected_size=10, attempts=1))
        self.assertFalse((self.dir / "a.zip").exists())

    def test_invalid_zip_fails(self):
        data = b"this is definitely not a zip"
        fake = FakeSession(FakeResponse([data], headers={"content-length": str(len(data))}))
        self.assertFalse(self._run(fake, attempts=1, verify_zip=True))

    def test_resume_uses_range_and_appends(self):
        data = zip_bytes()
        half = len(data) // 2
        (self.dir / "a.zip.part").write_bytes(data[:half])
        fake = FakeSession(
            FakeResponse(
                [data[half:]],
                status=206,
                headers={"content-length": str(len(data) - half)},
            )
        )
        self.assertTrue(self._run(fake, expected_size=len(data)))
        self.assertEqual(fake.calls[0]["headers"].get("Range"), f"bytes={half}-")
        self.assertEqual((self.dir / "a.zip").read_bytes(), data)

    def test_resume_complete_promotes_without_request(self):
        data = zip_bytes()
        (self.dir / "a.zip.part").write_bytes(data)
        fake = FakeSession(FakeResponse([]))
        self.assertTrue(self._run(fake, expected_size=len(data)))
        self.assertEqual(fake.calls, [])
        self.assertEqual((self.dir / "a.zip").read_bytes(), data)

    def test_range_ignored_restarts(self):
        data = zip_bytes()
        (self.dir / "a.zip.part").write_bytes(b"stale-partial")
        fake = FakeSession(
            FakeResponse([data], status=200, headers={"content-length": str(len(data))})
        )
        self.assertTrue(self._run(fake, expected_size=len(data)))
        self.assertEqual((self.dir / "a.zip").read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
