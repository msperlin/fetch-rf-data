import tempfile
import unittest
from pathlib import Path

from fcts.downloads import (
    ConfigError,
    check_disk_space,
    existing_bytes,
    load_metadata,
    make_metadata_entry,
    needs_download,
    save_metadata,
    validate_config,
)


class TestValidateConfig(unittest.TestCase):
    def test_valid(self):
        config = {
            "dirs": {"processing_dir": "~/x"},
            "urls": {"base_url": "http://example/"},
            "arquivos": {"files_to_download": ["Cnaes.zip"]},
        }
        validate_config(config)  # should not raise

    def test_missing_keys(self):
        with self.assertRaises(ConfigError):
            validate_config({"dirs": {}, "urls": {}, "arquivos": {}})

    def test_files_optional(self):
        config = {"dirs": {"processing_dir": "~/x"}, "urls": {"base_url": "http://x/"}}
        validate_config(config, require_files=False)  # should not raise


class TestMetadata(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        path = self.dir / "m.json"
        save_metadata(path, {"a.zip": {"size": 1}})
        self.assertEqual(load_metadata(path), {"a.zip": {"size": 1}})

    def test_missing_is_empty(self):
        self.assertEqual(load_metadata(self.dir / "nope.json"), {})

    def test_corrupt_is_empty(self):
        path = self.dir / "m.json"
        path.write_text("not json")
        self.assertEqual(load_metadata(path), {})

    def test_make_entry(self):
        entry = make_metadata_entry(
            {"url": "u", "size": 5, "last_modified": "d", "etag": "e"}
        )
        self.assertEqual(entry["url"], "u")
        self.assertEqual(entry["etag"], "e")
        self.assertIn("downloaded_at", entry)


class TestNeedsDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.info = {"name": "a.zip", "size": 10, "etag": "e1"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing(self):
        self.assertTrue(needs_download(self.info, self.dir, {}))

    def test_size_match_skips(self):
        (self.dir / "a.zip").write_bytes(b"0123456789")
        self.assertFalse(needs_download(self.info, self.dir, {}))

    def test_size_mismatch(self):
        (self.dir / "a.zip").write_bytes(b"012")
        self.assertTrue(needs_download(self.info, self.dir, {}))

    def test_empty_file(self):
        (self.dir / "a.zip").write_bytes(b"")
        self.assertTrue(needs_download(self.info, self.dir, {}))

    def test_etag_change(self):
        (self.dir / "a.zip").write_bytes(b"0123456789")
        meta = {"a.zip": {"etag": "old"}}
        self.assertTrue(needs_download(self.info, self.dir, meta))

    def test_etag_match_skips(self):
        (self.dir / "a.zip").write_bytes(b"0123456789")
        meta = {"a.zip": {"etag": "e1"}}
        self.assertFalse(needs_download(self.info, self.dir, meta))

    def test_force(self):
        (self.dir / "a.zip").write_bytes(b"0123456789")
        self.assertTrue(needs_download(self.info, self.dir, {}, force=True))


class TestDiskAndBytes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_bytes_prefers_part(self):
        info = {"name": "a.zip"}
        self.assertEqual(existing_bytes(self.dir, info), 0)
        (self.dir / "a.zip.part").write_bytes(b"123")
        (self.dir / "a.zip").write_bytes(b"12345")
        self.assertEqual(existing_bytes(self.dir, info), 3)

    def test_check_disk_space(self):
        (self.dir / "a.zip.part").write_bytes(b"12345")
        free, needed = check_disk_space(self.dir, [{"name": "a.zip", "size": 10}])
        self.assertGreater(free, 0)
        self.assertEqual(needed, 5)


if __name__ == "__main__":
    unittest.main()
