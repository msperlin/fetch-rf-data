"""Helpers for planning, validating and tracking downloads.

These functions have no network side effects, so they can be unit-tested in
isolation from the HTTP work done in :mod:`rfbpy.webdav`.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path

from tqdm import tqdm

METADATA_FILENAME = ".download_metadata.json"

log = logging.getLogger("rfbpy")


def metadata_path(dir_zip) -> Path:
    return Path(dir_zip) / METADATA_FILENAME


def load_metadata(path) -> dict:
    """Read the download metadata JSON, tolerating a missing/corrupt file."""

    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Ignoring unreadable metadata file: %s", path)
        return {}
    return data if isinstance(data, dict) else {}


def save_metadata(path, data) -> None:
    """Atomically write the download metadata JSON."""

    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def needs_download(file_info, dir_zip, metadata, force=False) -> bool:
    """Return ``True`` when ``file_info`` should be (re)downloaded.

    A local file is considered complete only when its size matches the remote
    size reported by WebDAV; when an ETag is known, a change in ETag also
    forces a re-download.
    """

    if force:
        return True

    target = Path(dir_zip) / file_info["name"]
    if not target.exists() or target.stat().st_size == 0:
        return True

    remote_size = file_info.get("size")
    if remote_size is not None and target.stat().st_size != remote_size:
        return True

    remote_etag = file_info.get("etag")
    local_etag = (metadata.get(file_info["name"]) or {}).get("etag")
    if remote_etag and local_etag and remote_etag != local_etag:
        return True

    return False


def existing_bytes(dir_zip, file_info) -> int:
    """Bytes already on disk for a file (finished file or ``.part``)."""

    target = Path(dir_zip) / file_info["name"]
    part = target.with_name(target.name + ".part")
    for candidate in (part, target):
        if candidate.exists():
            return candidate.stat().st_size
    return 0


def check_disk_space(dir_zip, files) -> tuple[int, int]:
    """Return ``(free_bytes, needed_bytes)`` for the planned downloads."""

    free = shutil.disk_usage(Path(dir_zip)).free
    needed = 0
    for file_info in files:
        size = file_info.get("size") or 0
        needed += max(0, size - existing_bytes(dir_zip, file_info))
    return free, needed


class TqdmLoggingHandler(logging.Handler):
    """Log handler that plays nicely with an active tqdm progress bar."""

    def emit(self, record):
        try:
            tqdm.write(self.format(record))
        except Exception:  # pragma: no cover - defensive, mirrors logging docs
            self.handleError(record)


class Progress:
    """Thread-safe aggregate byte progress bar."""

    def __init__(self, total, desc="Downloading"):
        self._lock = threading.Lock()
        self._bar = tqdm(
            total=max(int(total), 0) or None,
            unit="B",
            unit_scale=True,
            desc=desc,
            dynamic_ncols=True,
        )

    def update(self, n):
        with self._lock:
            self._bar.update(n)

    def close(self):
        with self._lock:
            self._bar.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def make_metadata_entry(file_info) -> dict:
    """Build the metadata record stored for a successfully downloaded file."""

    return {
        "url": file_info.get("url"),
        "size": file_info.get("size"),
        "last_modified": file_info.get("last_modified"),
        "etag": file_info.get("etag"),
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
