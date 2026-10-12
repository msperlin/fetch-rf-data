"""Source-vintage metadata ("stamps") attached to rfbpy outputs.

Every output (SQLite db, parquet folder, extracted-CSV folder, download
metadata) can record *which* Receita Federal snapshot it came from — the
monthly folder (``YYYY-MM``), the WebDAV URL, when the share was listed and
which files/etags were seen.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger("rfbpy")

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
SIDECAR_FILENAME = "_rfb_metadata.json"


def rfbpy_version() -> str:
    """Return the installed rfbpy version (lazy import to avoid cycles)."""

    try:
        from . import __version__
    except Exception:  # pragma: no cover - defensive
        return "unknown"
    return __version__


def now_iso() -> str:
    """Current local time as an ISO-ish timestamp."""

    return time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True)
class FileInfo:
    """A remote ``.zip`` file offered by the RFB share."""

    name: str
    url: str = ""
    size: int | None = None
    etag: str | None = None
    last_modified: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "FileInfo":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class SourceInfo:
    """Where an output's data came from (the RFB snapshot)."""

    month: str | None = None
    folder_url: str | None = None
    listed_at: str | None = None
    files: tuple[FileInfo, ...] = ()

    @property
    def known(self) -> bool:
        return bool(self.month or self.folder_url)

    @classmethod
    def unknown(cls, month: str | None = None) -> "SourceInfo":
        return cls(month=month if (month and MONTH_RE.match(str(month))) else None)

    @classmethod
    def from_dict(cls, data: dict | None) -> "SourceInfo":
        data = data or {}
        files = tuple(
            FileInfo.from_dict(item) for item in (data.get("files") or [])
        )
        return cls(
            month=data.get("source_month") or data.get("month"),
            folder_url=data.get("source_url") or data.get("folder_url"),
            listed_at=data.get("listed_at"),
            files=files,
        )

    def to_dict(self) -> dict:
        return {
            "rfbpy_version": rfbpy_version(),
            "source_month": self.month,
            "source_url": self.folder_url,
            "listed_at": self.listed_at,
            "files": [asdict(f) for f in self.files],
        }


def stamp(source: SourceInfo | None, **extra) -> dict:
    """Build the metadata mapping written to outputs.

    ``extra`` entries (e.g. ``generated_at``) are added on top of the source
    information; ``None`` values for source keys are filled in.
    """

    data = (source or SourceInfo()).to_dict()
    data.update(extra)
    return data


def write_sidecar(path, data: dict) -> Path:
    """Write a ``_rfb_metadata.json`` sidecar atomically."""

    path = Path(path)
    if path.is_dir():
        path = path / SIDECAR_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    log.info("Wrote source metadata: %s", path)
    return path


def read_sidecar(path) -> dict:
    """Read a sidecar written by :func:`write_sidecar` (tolerant)."""

    path = Path(path)
    if path.is_dir():
        path = path / SIDECAR_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
