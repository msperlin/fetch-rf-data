"""The single :class:`RFBData` object that owns the whole pipeline.

Typical use::

    from rfbpy import RFBData

    rfb = RFBData(
        processing_dir="~/Desktop/rfb_processing",
        files_to_download=["Cnaes.zip", "Estabelecimentos", "Empresas"],
    )
    rfb.download_and_unzip()
    rfb.to_sqlite()          # or rfb.to_parquet()

    # or everything at once
    rfb.run(destination="parquet")
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import downloads as dl
from . import extract as _extract
from . import tables as _tables
from . import webdav
from .cache import ManifestCache
from .config import (
    RFBConfig,
    build_config,
    normalize_files,
    override_config,
)
from .errors import ConfigError, DownloadError, ShareAccessError, UnavailableFilesError
from .loaders import parquet as parquet_loader
from .loaders import sqlite as sqlite_loader
from .loaders.duck import human_size

log = logging.getLogger("rfbpy")


@dataclass(frozen=True)
class FileInfo:
    """A remote ``.zip`` file offered by the RFB share."""

    name: str
    url: str
    size: int | None = None
    etag: str | None = None
    last_modified: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "FileInfo":
        return cls(
            name=data["name"],
            url=data.get("url", ""),
            size=data.get("size"),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
        )


@dataclass
class CheckReport:
    """What :meth:`RFBData.check` found on the share and locally."""

    month: str | None
    available: list[FileInfo] = field(default_factory=list)
    to_download: list[FileInfo] = field(default_factory=list)
    up_to_date: list[FileInfo] = field(default_factory=list)
    total_bytes: int = 0
    needed_bytes: int = 0
    free_bytes: int = 0
    base_url: str | None = None
    zip_dir: Path | None = None

    @property
    def missing(self) -> list[FileInfo]:
        return self.to_download

    @property
    def enough_disk(self) -> bool:
        return self.needed_bytes <= self.free_bytes

    def format(self) -> str:
        """Return a human-readable, printable summary of the report."""

        lines = ["RFB CNPJ — check", f"  Share      : {self.base_url or '(unknown)'}"]
        lines.append(f"  Month      : {self.month or '(unknown)'}")
        if self.zip_dir is not None:
            lines.append(f"  Zip folder : {self.zip_dir}")
        lines.append("")

        status = "ok" if self.enough_disk else "NOT ENOUGH SPACE"
        lines.append(f"  Files offered : {len(self.available)}")
        lines.append(f"  Up to date    : {len(self.up_to_date)}")
        lines.append(f"  To download   : {len(self.to_download)}")

        if self.to_download:
            lines.append("")
            lines.append("  Pending files:")
            width = max(len(f.name) for f in self.to_download)
            for info in self.to_download:
                size = human_size(info.size) if info.size else "?"
                lines.append(f"    - {info.name:<{width}}  {size:>10}")

        lines.append("")
        lines.append(
            f"  Download need : {human_size(self.needed_bytes)} "
            f"(total {human_size(self.total_bytes)})"
        )
        lines.append(f"  Disk free     : {human_size(self.free_bytes)}  [{status}]")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format()


@dataclass
class DownloadResult:
    """Outcome of :meth:`RFBData.download_and_unzip`."""

    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    bytes_downloaded: int = 0
    elapsed: float = 0.0
    dry_run: bool = False
    extract: "_extract.ExtractResult | None" = None

    @property
    def ok(self) -> bool:
        return not self.failed and (self.extract is None or self.extract.ok)


@dataclass
class LoadResult:
    """Outcome of :meth:`RFBData.to_sqlite` / :meth:`RFBData.to_parquet`."""

    destination: str
    path: Path
    tables: dict[str, int] = field(default_factory=dict)
    establishment_count: int | None = None
    elapsed: float = 0.0


@dataclass
class RunResult:
    """Outcome of the full :meth:`RFBData.run` pipeline."""

    download: DownloadResult
    extract: _extract.ExtractResult
    load: LoadResult | None = None

    @property
    def ok(self) -> bool:
        return self.download.ok and self.extract.ok


def _optional(value):
    """Treat ``0``/empty/None as 'not set'."""

    if value in (None, "", 0, "0"):
        return None
    return value


class RFBData:
    """Check the RFB share, download it and load it to SQLite or parquet.

    All options accepted by ``config.toml`` can be passed directly as keyword
    arguments.  Grouped dataclasses (``dirs=``, ``urls=``, ...) and a whole
    :class:`~rfbpy.RFBConfig` are also accepted, and ``RFBData.from_toml``
    loads an existing ``config.toml``.
    """

    def __init__(self, config: RFBConfig | None = None, *, verbose=False, logger=None, **kwargs):
        if config is None:
            config = build_config(**kwargs)
        elif kwargs:
            config = override_config(config, **kwargs)
        if not isinstance(config, RFBConfig):
            raise ConfigError("config must be an RFBConfig instance")

        self.config = config.validate()
        self.paths = config.dirs.paths()
        self.log = logger or log
        if verbose:
            logging.getLogger("rfbpy").setLevel(logging.DEBUG)

    # -- construction helpers ------------------------------------------------

    @classmethod
    def from_toml(cls, path, **kwargs) -> "RFBData":
        """Build an :class:`RFBData` from a ``config.toml`` file."""

        return cls(config=RFBConfig.from_toml(path), **kwargs)

    # -- directories ---------------------------------------------------------

    @property
    def processing_dir(self) -> Path:
        return self.paths["processing"]

    @property
    def zip_dir(self) -> Path:
        return self.paths["zip"]

    @property
    def unzip_dir(self) -> Path:
        return self.paths["unzip"]

    @property
    def sqlite_dir(self) -> Path:
        return self.paths["sqlite"]

    @property
    def parquet_dir(self) -> Path:
        return self.paths["parquet"]

    @property
    def cache_path(self) -> Path:
        """Path of the file-manifest cache (see ``[cache]``)."""

        return self.paths["cache"] / "files_manifest.json"

    def _manifest_cache(self):
        """Return a :class:`ManifestCache`, or ``None`` when disabled."""

        if not self.config.cache.enabled:
            return None
        return ManifestCache(
            self.cache_path,
            ttl_seconds=float(self.config.cache.ttl_hours) * 3600,
        )

    def _ensure(self, *dirs: Path) -> None:
        for directory in dirs:
            Path(directory).mkdir(parents=True, exist_ok=True)

    # -- WebDAV plumbing -----------------------------------------------------

    def _auth(self):
        token = os.environ.get("RFB_SHARE_TOKEN") or self.config.urls.share_token
        return (token, "") if token else None

    def _discovery_kwargs(self) -> dict:
        d = self.config.downloads
        return {
            "auth": self._auth(),
            "connect_timeout": d.connect_timeout,
            "read_timeout": d.read_timeout,
            "user_agent": d.user_agent,
        }

    def _desired_files(self, only) -> list[str]:
        if only:
            if isinstance(only, str):
                return [s.strip() for s in only.split(",") if s.strip()]
            return normalize_files(only)
        return normalize_files(self.config.arquivos.files_to_download)

    def _resolve_month(self, month) -> str:
        if month is not None:
            return month
        return self.config.urls.month

    # -- discovery -----------------------------------------------------------

    def list_months(self) -> list[str]:
        """Return the sorted ``YYYY-MM`` folders available on the share."""

        cfg = self.config
        return webdav.list_months(
            cfg.urls.base_url,
            cfg.downloads.request_timeout,
            **self._discovery_kwargs(),
        )

    def _list_remote(self, only, month, refresh=False) -> list[dict]:
        """List the requested remote files, erroring on unmatched requests.

        The full manifest is cached on disk for ``[cache].ttl_hours`` (24 h by
        default); set ``refresh=True`` (or ``cache_enabled=False``) to bypass it.
        """

        cfg = self.config
        resolved = self._resolve_month(month)
        key = f"{cfg.urls.base_url}|{resolved}"

        cache = self._manifest_cache()
        files = None
        if cache is not None and not refresh:
            hit = cache.get(key)
            if hit is not None:
                files, age = hit
                log.info(
                    "Using cached file manifest: %s (%d file(s), %.1f h old; "
                    "expires in %.1f h)",
                    cache.path,
                    len(files),
                    age / 3600,
                    (cache.ttl_seconds - age) / 3600,
                )

        if files is None:
            files = webdav.list_files(
                cfg.urls.base_url,
                cfg.downloads.request_timeout,
                month=resolved,
                **self._discovery_kwargs(),
            )
            if cache is not None:
                cache.set(key, files)
                log.info(
                    "Saved file manifest cache: %s (%d file(s), valid for %.0f h)",
                    cache.path,
                    len(files),
                    cache.ttl_seconds / 3600,
                )

        matched, unmatched = webdav.match_files(files, self._desired_files(only))
        if unmatched:
            raise UnavailableFilesError(
                unmatched,
                files,
                month=resolved,
                base_url=cfg.urls.base_url,
            )
        return matched

    def discover(self, only=None, month=None) -> list[FileInfo]:
        """List the remote files matching ``only`` (or the configured list)."""

        return [FileInfo.from_dict(item) for item in self._list_remote(only, month)]

    # -- planning / checking -------------------------------------------------

    def _plan(self, only, month, force):
        self._ensure(self.zip_dir)
        raw = self._list_remote(only, month, refresh=force)
        meta_file = dl.metadata_path(self.zip_dir)
        metadata = dl.load_metadata(meta_file)
        to_download, up_to_date = [], []
        for item in raw:
            if force or dl.needs_download(item, self.zip_dir, metadata):
                to_download.append(item)
            else:
                log.info("Skipping existing file: %s", item["name"])
                up_to_date.append(item)
        return raw, to_download, up_to_date, metadata, meta_file

    def check(self, only=None, month=None, force=False, print_report=True) -> CheckReport:
        """Report available files and which ones still need downloading.

        Nothing is downloaded and no network request is made to the files
        themselves; this only lists the WebDAV collection and inspects the
        local directory.  A readable summary is printed to stdout (set
        ``print_report=False`` to only get the returned object).
        """

        raw, to_download, up_to_date, _metadata, _meta = self._plan(
            only, month, force
        )
        free, needed = dl.check_disk_space(self.zip_dir, to_download)
        total = sum((item.get("size") or 0) for item in raw)
        report = CheckReport(
            month=self._resolve_month(month),
            available=[FileInfo.from_dict(i) for i in raw],
            to_download=[FileInfo.from_dict(i) for i in to_download],
            up_to_date=[FileInfo.from_dict(i) for i in up_to_date],
            total_bytes=total,
            needed_bytes=needed,
            free_bytes=free,
            base_url=self.config.urls.base_url,
            zip_dir=self.zip_dir,
        )
        if print_report:
            print(report.format())
        return report

    # -- download ------------------------------------------------------------

    def download_and_unzip(
        self,
        only=None,
        month=None,
        force=False,
        dry_run=False,
        show_progress=None,
    ) -> DownloadResult:
        """Download the missing files and extract the archives.

        Downloads only what is missing or out of date (disk-space checks,
        retries, resumable ``.part`` files and zip verification are handled
        here), then runs :meth:`unzip`.  The extraction outcome is available as
        ``result.extract``.  ``dry_run=True`` skips both downloading and
        extraction.
        """

        result = self._download(
            only=only,
            month=month,
            force=force,
            dry_run=dry_run,
            show_progress=show_progress,
        )
        if not dry_run:
            result.extract = self.unzip()
        return result

    def _download(
        self,
        only=None,
        month=None,
        force=False,
        dry_run=False,
        show_progress=None,
    ) -> DownloadResult:
        """Download the files that are missing or out of date."""

        cfg = self.config
        d = cfg.downloads
        started = time.time()

        raw, to_download, up_to_date, metadata, meta_file = self._plan(
            only, month, force
        )
        skipped = [item["name"] for item in up_to_date]
        planned = [item["name"] for item in to_download]

        if not raw:
            log.info("Nothing to do: no matching files.")
            return DownloadResult(skipped=skipped, elapsed=time.time() - started)
        if not to_download:
            log.info("Nothing to download: everything is up to date.")
            return DownloadResult(skipped=skipped, elapsed=time.time() - started)
        if dry_run:
            log.info("Dry run: %d file(s) would be downloaded:", len(to_download))
            for item in to_download:
                log.info("  %s (%s bytes)", item["name"], item.get("size"))
            return DownloadResult(
                planned=planned,
                skipped=skipped,
                dry_run=True,
                elapsed=time.time() - started,
            )

        free, needed = dl.check_disk_space(self.zip_dir, to_download)
        log.info(
            "Disk free: %.1f GiB | needed: %.1f GiB",
            free / 2**30,
            needed / 2**30,
        )
        if needed > free:
            raise DownloadError(
                f"Not enough free disk space in {self.zip_dir} "
                f"(need {needed / 2**30:.1f} GiB, have {free / 2**30:.1f} GiB)."
            )

        max_workers = max(1, min(int(d.max_concurrent_downloads), len(to_download)))
        large_threshold = float(d.large_file_threshold_mb) * 1024 * 1024
        small = [f for f in to_download if (f.get("size") or 0) < large_threshold]
        large = [f for f in to_download if (f.get("size") or 0) >= large_threshold]
        max_large = max(1, min(int(d.max_large_downloads), len(large))) if large else 0

        stop_event = threading.Event()
        lock = threading.Lock()
        counters = {"ok": 0, "failed": 0, "bytes": 0}
        downloaded: list[str] = []
        failed: list[str] = []

        total_bytes = sum(
            max(0, (f.get("size") or 0) - dl.existing_bytes(self.zip_dir, f))
            for f in to_download
        )
        show = cfg.sqlite.show_progress if show_progress is None else show_progress
        progress = dl.Progress(total_bytes) if show and total_bytes > 0 else None
        progress_cb = progress.update if progress else None

        def run_batch(batch, workers):
            if not batch:
                return
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
            futures = {
                executor.submit(
                    webdav.download_with_retries,
                    file_info["url"],
                    self.zip_dir,
                    d.request_timeout,
                    attempts=int(d.max_attempts),
                    backoff=d.backoff,
                    auth=self._auth(),
                    expected_size=file_info.get("size"),
                    resume=bool(d.resume),
                    verify_zip=bool(d.verify_zip),
                    connect_timeout=d.connect_timeout,
                    read_timeout=d.read_timeout,
                    user_agent=d.user_agent,
                    progress_cb=progress_cb,
                    show_progress_bar=show,
                    stop_event=stop_event,
                    name=file_info["name"],
                ): file_info
                for file_info in batch
            }
            try:
                for future in concurrent.futures.as_completed(futures):
                    file_info = futures[future]
                    try:
                        ok = future.result()
                    except Exception as exc:  # noqa: BLE001 - keep going on one failure
                        log.error(
                            "Unhandled error downloading %s: %s",
                            file_info["name"], exc,
                        )
                        ok = False
                    with lock:
                        if ok:
                            counters["ok"] += 1
                            counters["bytes"] += file_info.get("size") or 0
                            downloaded.append(file_info["name"])
                            metadata[file_info["name"]] = dl.make_metadata_entry(file_info)
                        else:
                            counters["failed"] += 1
                            failed.append(file_info["name"])
            except BaseException:
                # Abort promptly (e.g. Ctrl-C) but let workers stop and keep
                # .part files for a later resume.
                stop_event.set()
                for future in futures:
                    future.cancel()
                raise
            finally:
                executor.shutdown(wait=True)

        log.info("Starting downloads (%d small, %d large)...", len(small), len(large))
        try:
            run_batch(small, max_workers)
            run_batch(large, max_large or max_workers)
        except KeyboardInterrupt:
            log.warning(
                "Interrupted by user; stopped new downloads. "
                "Partial files are kept for resume."
            )
            dl.save_metadata(meta_file, metadata)
            raise
        finally:
            if progress is not None:
                progress.close()

        dl.save_metadata(meta_file, metadata)
        elapsed = time.time() - started
        log.info(
            "Summary: %d downloaded, %d skipped, %d failed. Elapsed: %.1f s",
            counters["ok"], len(skipped), counters["failed"], elapsed,
        )
        return DownloadResult(
            downloaded=downloaded,
            skipped=skipped,
            failed=failed,
            planned=planned,
            bytes_downloaded=counters["bytes"],
            elapsed=elapsed,
        )

    # -- extraction ----------------------------------------------------------

    def unzip(self, show_progress=None) -> _extract.ExtractResult:
        """Extract every downloaded ``.zip`` (idempotent)."""

        cfg = self.config
        return _extract.extract_archives(
            self.zip_dir,
            self.unzip_dir,
            skip_existing=bool(cfg.unzip.skip_existing),
            remove_zip_after_extract=bool(cfg.unzip.remove_zip_after_extract),
        )

    # -- loading -------------------------------------------------------------

    def _build_tables(self):
        patterns = _tables.enabled_patterns(self.config.arquivos.files_to_download)
        code_tables = _tables.build_code_tables(patterns)
        big_tables = _tables.build_big_tables(self.config.colunas, patterns)
        return code_tables, big_tables

    def _db_path(self) -> Path:
        sqlite = self.config.sqlite
        name = str(sqlite.db_filename).replace(
            "{date}", time.strftime(sqlite.db_date_format)
        )
        return self.sqlite_dir / name

    def _temp_dir(self) -> Path:
        return Path(self.config.sqlite.duckdb_temp_dir or (self.processing_dir / "duckdb_tmp"))

    def to_sqlite(self, path=None) -> LoadResult:
        """Load the extracted CSVs into a SQLite database."""

        cfg = self.config
        code_tables, big_tables = self._build_tables()
        self._ensure(self.unzip_dir, self.sqlite_dir)

        db_path = Path(path) if path else self._db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.exists():
            log.warning("File %s already exists; removing it.", db_path)
            db_path.unlink()

        log.info("SQLite output: %s", db_path)
        started = time.time()
        rows = sqlite_loader.load_tables(
            db_path,
            self.unzip_dir,
            code_tables,
            big_tables,
            separator=cfg.leitura.separator,
            encoding=cfg.leitura.encoding,
            threads=_optional(cfg.sqlite.duckdb_threads),
            memory_limit=_optional(cfg.sqlite.duckdb_memory_limit),
            temp_directory=self._temp_dir(),
            null_padding=bool(cfg.sqlite.duckdb_null_padding),
            show_progress=bool(cfg.sqlite.show_progress),
        )
        if cfg.leitura.remove_extracted_after_use:
            sqlite_loader.remove_source_files(self.unzip_dir, code_tables, big_tables)

        count = sqlite_loader.finalize(
            db_path,
            cfg.sqlite.load_pragmas,
            cfg.sqlite.final_pragmas,
            show_progress=bool(cfg.sqlite.show_progress),
        )
        return LoadResult(
            destination="sqlite",
            path=db_path,
            tables=rows,
            establishment_count=count,
            elapsed=time.time() - started,
        )

    def to_parquet(self, path=None) -> LoadResult:
        """Load the extracted CSVs into one parquet file per table."""

        cfg = self.config
        code_tables, big_tables = self._build_tables()
        self._ensure(self.unzip_dir)

        out_dir = Path(path) if path else self.parquet_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        log.info("Parquet output: %s", out_dir)
        started = time.time()
        rows = parquet_loader.load_tables(
            out_dir,
            self.unzip_dir,
            code_tables,
            big_tables,
            separator=cfg.leitura.separator,
            encoding=cfg.leitura.encoding,
            compression=cfg.parquet.compression,
            threads=_optional(cfg.sqlite.duckdb_threads),
            memory_limit=_optional(cfg.sqlite.duckdb_memory_limit),
            temp_directory=self._temp_dir(),
            null_padding=bool(cfg.sqlite.duckdb_null_padding),
            show_progress=bool(cfg.sqlite.show_progress),
        )
        if cfg.leitura.remove_extracted_after_use:
            sqlite_loader.remove_source_files(self.unzip_dir, code_tables, big_tables)

        return LoadResult(
            destination="parquet",
            path=out_dir,
            tables=rows,
            establishment_count=rows.get("estabelecimento"),
            elapsed=time.time() - started,
        )

    # -- orchestration -------------------------------------------------------

    def run(
        self,
        destination="sqlite",
        only=None,
        month=None,
        force=False,
        dry_run=False,
    ) -> RunResult:
        """Download, extract and load in one call."""

        destination = str(destination).lower()
        if destination not in ("sqlite", "parquet"):
            raise ValueError(
                f"Unknown destination {destination!r} (use 'sqlite' or 'parquet')"
            )

        download_result = self.download_and_unzip(
            only=only, month=month, force=force, dry_run=dry_run
        )
        extract_result = download_result.extract or _extract.ExtractResult()
        load_result = None
        if not dry_run:
            load_result = (
                self.to_sqlite() if destination == "sqlite" else self.to_parquet()
            )
        return RunResult(
            download=download_result, extract=extract_result, load=load_result
        )

    def __repr__(self) -> str:
        return (
            f"RFBData(processing_dir={str(self.processing_dir)!r}, "
            f"month={self.config.urls.month!r}, "
            f"files={len(self.config.arquivos.files_to_download)})"
        )
