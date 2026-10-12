import json

from rfbpy.cache import ManifestCache


def test_set_and_get_roundtrip(tmp_path):
    cache = ManifestCache(tmp_path / "m.json", ttl_seconds=10, clock=lambda: 100)
    cache.set("k", [{"name": "a.zip"}])
    files, age = cache.get("k")
    assert files == [{"name": "a.zip"}]
    assert age == 0
    assert (tmp_path / "m.json").exists()


def test_expiry(tmp_path):
    now = {"t": 1000.0}
    cache = ManifestCache(tmp_path / "m.json", ttl_seconds=100, clock=lambda: now["t"])
    cache.set("k", [{"name": "a.zip"}])

    now["t"] = 1099.0  # still fresh
    assert cache.get("k") is not None

    now["t"] = 1100.0  # exactly at the TTL -> expired
    assert cache.get("k") is None


def test_clock_going_backwards_invalidates(tmp_path):
    now = {"t": 1000.0}
    cache = ManifestCache(tmp_path / "m.json", ttl_seconds=100, clock=lambda: now["t"])
    cache.set("k", [{"name": "a.zip"}])
    now["t"] = 900.0
    assert cache.get("k") is None


def test_missing_key(tmp_path):
    cache = ManifestCache(tmp_path / "m.json", ttl_seconds=10, clock=lambda: 0)
    assert cache.get("nope") is None


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("not json")
    cache = ManifestCache(path, ttl_seconds=10, clock=lambda: 0)
    assert cache.get("k") is None
    cache.set("k", [])  # overwrites the corrupt file
    assert cache.get("k") == ([], 0)


def test_wrong_version_is_ignored(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"version": 999, "entries": {"k": {"saved_at": 0, "files": []}}}))
    cache = ManifestCache(path, ttl_seconds=10, clock=lambda: 0)
    assert cache.get("k") is None


def test_clear(tmp_path):
    cache = ManifestCache(tmp_path / "m.json", ttl_seconds=10, clock=lambda: 0)
    cache.set("k", [])
    cache.clear()
    assert not (tmp_path / "m.json").exists()
    cache.clear()  # idempotent
