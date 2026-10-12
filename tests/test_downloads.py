from rfbpy.downloads import (
    check_disk_space,
    existing_bytes,
    load_metadata,
    make_metadata_entry,
    needs_download,
    save_metadata,
)


def test_metadata_roundtrip(tmp_path):
    path = tmp_path / "m.json"
    save_metadata(path, {"a.zip": {"size": 1}})
    assert load_metadata(path) == {"a.zip": {"size": 1}}


def test_metadata_missing_is_empty(tmp_path):
    assert load_metadata(tmp_path / "nope.json") == {}


def test_metadata_corrupt_is_empty(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("not json")
    assert load_metadata(path) == {}


def test_make_metadata_entry():
    entry = make_metadata_entry(
        {"url": "u", "size": 5, "last_modified": "d", "etag": "e"}
    )
    assert entry["url"] == "u"
    assert entry["etag"] == "e"
    assert "downloaded_at" in entry


def test_needs_download_missing(tmp_path):
    assert needs_download({"name": "a.zip", "size": 10, "etag": "e1"}, tmp_path, {})


def test_needs_download_size_match_skips(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"0123456789")
    assert not needs_download({"name": "a.zip", "size": 10, "etag": "e1"}, tmp_path, {})


def test_needs_download_size_mismatch(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"012")
    assert needs_download({"name": "a.zip", "size": 10}, tmp_path, {})


def test_needs_download_empty_file(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"")
    assert needs_download({"name": "a.zip", "size": 10}, tmp_path, {})


def test_needs_download_etag_change(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"0123456789")
    assert needs_download(
        {"name": "a.zip", "size": 10, "etag": "e1"}, tmp_path, {"a.zip": {"etag": "old"}}
    )


def test_needs_download_etag_match_skips(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"0123456789")
    assert not needs_download(
        {"name": "a.zip", "size": 10, "etag": "e1"}, tmp_path, {"a.zip": {"etag": "e1"}}
    )


def test_needs_download_force(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"0123456789")
    assert needs_download({"name": "a.zip", "size": 10}, tmp_path, {}, force=True)


def test_existing_bytes_prefers_part(tmp_path):
    info = {"name": "a.zip"}
    assert existing_bytes(tmp_path, info) == 0
    (tmp_path / "a.zip.part").write_bytes(b"123")
    (tmp_path / "a.zip").write_bytes(b"12345")
    assert existing_bytes(tmp_path, info) == 3


def test_check_disk_space(tmp_path):
    (tmp_path / "a.zip.part").write_bytes(b"12345")
    free, needed = check_disk_space(tmp_path, [{"name": "a.zip", "size": 10}])
    assert free > 0
    assert needed == 5
