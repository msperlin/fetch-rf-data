"""Extraction of the downloaded ``.zip`` archives.

The extraction is idempotent by default: archives whose members already exist
(with the same size) are skipped, which makes the process resumable.
"""

from __future__ import annotations

import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("rfbpy")


@dataclass
class ExtractResult:
    """Outcome of :func:`extract_archives`."""

    extracted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def total(self) -> int:
        return len(self.extracted) + len(self.skipped) + len(self.failed)


def unzip_if_needed(zip_path, target_dir, skip_existing=True):
    """Extract ``zip_path`` into ``target_dir`` only when necessary.

    Returns ``True`` when an extraction was performed and ``False`` when all
    members already existed (same size) and ``skip_existing`` is true.  Raises
    ``ValueError`` if a member points outside ``target_dir`` (Zip Slip guard).
    """

    target_dir = Path(target_dir).resolve()

    with zipfile.ZipFile(zip_path) as zip_ref:
        members = [info for info in zip_ref.infolist() if not info.is_dir()]

        already_extracted = (
            skip_existing
            and members
            and all(
                (dest := target_dir / info.filename).is_file()
                and dest.stat().st_size == info.file_size
                for info in members
            )
        )
        if already_extracted:
            return False

        for info in members:
            dest = (target_dir / info.filename).resolve()
            if not dest.is_relative_to(target_dir):
                raise ValueError(f"unsafe member path: {info.filename}")

        zip_ref.extractall(target_dir)
        return True


def extract_archives(
    zip_dir,
    unzip_dir,
    *,
    skip_existing: bool = True,
    remove_zip_after_extract: bool = False,
) -> ExtractResult:
    """Extract every ``*.zip`` from ``zip_dir`` into ``unzip_dir``."""

    zip_dir = Path(zip_dir)
    unzip_dir = Path(unzip_dir)
    unzip_dir.mkdir(parents=True, exist_ok=True)

    result = ExtractResult()
    zip_files = sorted(zip_dir.glob("*.zip"))
    if not zip_files:
        log.warning("No .zip files found in %s", zip_dir)
        return result

    log.info("Found %d zip file(s) in %s", len(zip_files), zip_dir)
    started = time.perf_counter()

    for idx, zip_path in enumerate(zip_files, start=1):
        log.info("[%d/%d] Processing %s", idx, len(zip_files), zip_path.name)
        try:
            if unzip_if_needed(zip_path, unzip_dir, skip_existing):
                result.extracted.append(zip_path.name)
                if remove_zip_after_extract:
                    zip_path.unlink()
                    log.info("Removed archive: %s", zip_path.name)
            else:
                log.info("Already extracted, skipping: %s", zip_path.name)
                result.skipped.append(zip_path.name)
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            log.error("Failed to extract %s: %s", zip_path.name, exc)
            result.failed.append(zip_path.name)

    result.elapsed = time.perf_counter() - started
    log.info(
        "Done. %d extracted, %d skipped, %d failed in %.1f s",
        len(result.extracted),
        len(result.skipped),
        len(result.failed),
        result.elapsed,
    )
    return result
