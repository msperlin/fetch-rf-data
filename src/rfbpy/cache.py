"""On-disk cache for the remote file manifest (the WebDAV listing).

Listing the public share can be slow and flaky, so the discovered ``.zip``
manifest is stored in a JSON file and reused until it expires (one day by
default).  The cache is keyed by ``base_url`` + month, so different shares or
months do not collide.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("rfbpy")

CACHE_VERSION = 1
DEFAULT_FILENAME = "files_manifest.json"


class ManifestCache:
    """A small JSON cache of the remote file manifest with a TTL.

    ``ttl_seconds`` is how long an entry stays valid.  ``clock`` is injectable
    for tests.
    """

    def __init__(self, path, ttl_seconds=86400, clock=time.time):
        self.path = Path(path)
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock

    def get(self, key):
        """Return ``(files, age_seconds)`` when a fresh entry exists, else None."""

        entry = self._read().get(key)
        if not isinstance(entry, dict):
            return None
        saved_at = entry.get("saved_at")
        payload = entry.get("files")
        if not isinstance(saved_at, (int, float)) or payload is None:
            return None
        age = self._clock() - saved_at
        if age < 0 or age >= self.ttl_seconds:
            return None
        return payload, age

    def set(self, key, files) -> None:
        """Store ``files`` for ``key``, refreshing the timestamp."""

        entries = self._read()
        entries[key] = {"saved_at": self._clock(), "files": files}
        self._write(entries)

    def clear(self) -> None:
        """Remove the cache file (all keys)."""

        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return {}
        entries = data.get("entries")
        return entries if isinstance(entries, dict) else {}

    def _write(self, entries) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": CACHE_VERSION, "entries": entries}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.path)
