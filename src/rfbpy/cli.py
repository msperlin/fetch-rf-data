"""Command-line interface for rfbpy.

Reads a ``config.toml`` (``--config``, ``RFB_CONFIG`` or ``./config.toml``) and
wraps the :class:`~rfbpy.RFBData` methods.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .core import RFBData
from .downloads import TqdmLoggingHandler
from .errors import RFBError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfbpy",
        description="Download, extract and load the Receita Federal CNPJ open data.",
    )
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("months", help="list the available monthly folders")

    check = sub.add_parser("check", help="show what is missing or out of date")
    _add_selection(check)

    download = sub.add_parser(
        "download-and-unzip",
        aliases=["download"],
        help="download the missing files and extract the archives",
    )
    _add_selection(download)
    download.add_argument("--dry-run", action="store_true", help="do not download")
    download.add_argument("--jobs", type=int, help="max concurrent downloads")
    download.add_argument("--no-resume", action="store_true", help="do not resume .part files")
    download.add_argument("--no-verify", action="store_true", help="skip the zip integrity check")
    download.add_argument("--no-progress", action="store_true", help="disable progress bars")

    sub.add_parser("unzip", help="extract the downloaded archives")

    load = sub.add_parser("load", help="load the extracted CSVs")
    load.add_argument("--dest", choices=["sqlite", "parquet"], default="sqlite")
    load.add_argument("--path", help="output file (sqlite) or folder (parquet)")

    run = sub.add_parser("run", help="download, extract and load in one call")
    _add_selection(run)
    run.add_argument("--dest", choices=["sqlite", "parquet"], default="sqlite")
    run.add_argument("--dry-run", action="store_true", help="do not download/load")

    return parser


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--only", help="comma-separated substrings to select files")
    parser.add_argument("--month", help="'latest', a YYYY-MM folder, or empty")
    parser.add_argument("--force", action="store_true", help="re-download existing files")


def setup_logging(verbose: bool = False) -> None:
    handler = TqdmLoggingHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def _resolve_config(explicit: str | None) -> Path:
    path = explicit or os.environ.get("RFB_CONFIG") or "config.toml"
    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise RFBError(
            f"Config file not found: {resolved}. Pass --config or set RFB_CONFIG."
        )
    return resolved


def _load(args) -> RFBData:
    return RFBData.from_toml(_resolve_config(args.config), verbose=args.verbose)


def _apply(args, data: RFBData) -> RFBData:
    """Apply CLI overrides (jobs / resume / verify / progress) to the object."""

    if getattr(args, "jobs", None):
        data.config.downloads.max_concurrent_downloads = args.jobs
    if getattr(args, "no_resume", False):
        data.config.downloads.resume = False
    if getattr(args, "no_verify", False):
        data.config.downloads.verify_zip = False
    if getattr(args, "no_progress", False):
        data.config.sqlite.show_progress = False
    return data


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    try:
        data = _apply(args, _load(args))
    except RFBError as exc:
        logging.error("%s", exc)
        return 1

    try:
        if args.command == "months":
            for month in data.list_months():
                print(month)
            return 0

        if args.command == "check":
            report = data.check(
                only=args.only, month=args.month, force=args.force, print_report=False
            )
            print(report.format())
            return 0

        if args.command in ("download-and-unzip", "download"):
            result = data.download_and_unzip(
                only=args.only,
                month=args.month,
                force=args.force,
                dry_run=args.dry_run,
            )
            if result.dry_run:
                for name in result.planned:
                    print(name)
                return 0
            extracted = result.extract.extracted if result.extract else []
            print(
                f"{len(result.downloaded)} downloaded, "
                f"{len(extracted)} extracted, "
                f"{len(result.skipped)} skipped, "
                f"{len(result.failed)} failed "
                f"({result.bytes_downloaded} bytes) in {result.elapsed:.1f}s"
            )
            return 1 if result.failed else 0

        if args.command == "unzip":
            result = data.unzip()
            print(
                f"{len(result.extracted)} extracted, "
                f"{len(result.skipped)} skipped, "
                f"{len(result.failed)} failed"
            )
            return 1 if result.failed else 0

        if args.command == "load":
            result = (
                data.to_sqlite(path=args.path)
                if args.dest == "sqlite"
                else data.to_parquet(path=args.path)
            )
            print(f"{result.destination}: {result.path} ({result.elapsed:.1f}s)")
            for name, rows in result.tables.items():
                print(f"  {name}: {rows} rows")
            return 0

        if args.command == "run":
            result = data.run(
                destination=args.dest,
                only=args.only,
                month=args.month,
                force=args.force,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                for name in result.download.planned:
                    print(name)
                return 0
            print(
                f"{len(result.download.downloaded)} downloaded, "
                f"{len(result.extract.extracted)} extracted"
            )
            if result.load is not None:
                print(f"{result.load.destination}: {result.load.path}")
            return 0 if result.ok else 1
    except RFBError as exc:
        logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.warning("Interrupted by user; partial files are kept for resume.")
        return 130

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
