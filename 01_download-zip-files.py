# -*- coding: utf-8 -*-
"""
código original: https://github.com/rictom/cnpj-sqlite

Esta versão é modificada para baixar apenas os dados mais interessantes
para projeto SEBRAE.

O site de dados públicos da Receita Federal foi migrado para um
compartilhamento Nextcloud. Os arquivos ficam em pastas mensais
(YYYY-MM) e são acessados via WebDAV:

https://arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK

As URLs base e o token do compartilhamento ficam em config.toml ([urls]).
O token também pode ser definido pela variável de ambiente RFB_SHARE_TOKEN.

Exemplos:
    python 01_download-zip-files.py
    python 01_download-zip-files.py --only Cnaes,Paises,Motivos
    python 01_download-zip-files.py --month 2026-08 --jobs 4
    python 01_download-zip-files.py --list-months
    python 01_download-zip-files.py --dry-run
"""

import argparse
import concurrent.futures
import logging
import os
import sys
import threading
import time
from pathlib import Path

from fcts.downloads import (
    ConfigError,
    Progress,
    TqdmLoggingHandler,
    check_disk_space,
    existing_bytes,
    load_metadata,
    make_metadata_entry,
    metadata_path,
    needs_download,
    save_metadata,
    validate_config,
)
from fcts.utils import get_config, get_dir_names
from fcts.webscrapping import (
    ShareAccessError,
    download_with_retries,
    get_all_links,
    list_months,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Download the open CNPJ data published by Receita Federal."
    )
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--jobs", type=int, help="max concurrent downloads")
    parser.add_argument(
        "--month",
        help="'latest' (default), a specific YYYY-MM folder, or empty",
    )
    parser.add_argument(
        "--only",
        help="comma-separated substrings to download (overrides files_to_download)",
    )
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be downloaded without downloading",
    )
    parser.add_argument("--list-months", action="store_true", help="list available months and exit")
    parser.add_argument("--no-resume", action="store_true", help="do not resume .part files")
    parser.add_argument("--no-verify", action="store_true", help="skip the zip integrity check")
    parser.add_argument("--no-progress", action="store_true", help="disable progress bars")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def setup_logging(verbose=False):
    handler = TqdmLoggingHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def main(argv=None):
    args = parse_args(argv)
    setup_logging(args.verbose)

    try:
        config = get_config(args.config)
        validate_config(config, require_files=not args.only)
    except (FileNotFoundError, ConfigError) as exc:
        logging.error("%s", exc)
        return 1

    dirs = config.get("dirs", {})
    urls = config.get("urls", {})
    downloads = config.get("downloads", {})
    arquivos = config.get("arquivos", {})

    max_workers = args.jobs or downloads.get(
        "max_concurrent_downloads", downloads.get("n_cores", 3)
    )
    request_timeout = downloads.get("request_timeout", 30)
    connect_timeout = downloads.get("connect_timeout")
    read_timeout = downloads.get("read_timeout")
    attempts = int(downloads.get("max_attempts", 3))
    backoff = downloads.get("backoff", 2)
    large_threshold = int(downloads.get("large_file_threshold_mb", 100)) * 1024 * 1024
    max_large = downloads.get("max_large_downloads", max_workers)
    resume = bool(downloads.get("resume", True)) and not args.no_resume
    verify_zip = bool(downloads.get("verify_zip", True)) and not args.no_verify
    user_agent = downloads.get("user_agent")

    if args.only:
        desired_files = [s.strip() for s in args.only.split(",") if s.strip()]
    else:
        desired_files = arquivos.get("files_to_download")

    base_url = urls.get("base_url")
    share_token = os.environ.get("RFB_SHARE_TOKEN") or urls.get("share_token")
    auth = (share_token, "") if share_token else None
    month = args.month if args.month is not None else urls.get("month", "latest")

    my_dirs = get_dir_names(config)
    dir_zip = Path(dirs["processing_dir"]).expanduser() / my_dirs[0]
    dir_zip.mkdir(parents=True, exist_ok=True)

    discovery_kwargs = dict(
        auth=auth,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        user_agent=user_agent,
    )

    # --- optional: just list available months -------------------------------
    if args.list_months:
        try:
            months = list_months(base_url, request_timeout, **discovery_kwargs)
        except Exception as exc:  # noqa: BLE001 - report cleanly and exit
            logging.error("Could not list months: %s", exc)
            return 1
        if not months:
            logging.info("No monthly folders found at %s", base_url)
        for m in months:
            print(m)
        return 0

    # --- discovery ----------------------------------------------------------
    try:
        files = get_all_links(
            base_url,
            desired_files,
            request_timeout,
            month=month,
            **discovery_kwargs,
        )
    except ShareAccessError as exc:
        logging.error("%s", exc)
        return 1

    if not files:
        logging.info("Nothing to do: no matching files.")
        return 0

    # --- plan ---------------------------------------------------------------
    meta_file = metadata_path(dir_zip)
    metadata = load_metadata(meta_file)

    to_download = []
    for file_info in files:
        if args.force or needs_download(file_info, dir_zip, metadata):
            to_download.append(file_info)
        else:
            logging.info("Skipping existing file: %s", file_info["name"])

    skipped = len(files) - len(to_download)

    if args.dry_run:
        logging.info("Dry run: %d file(s) would be downloaded:", len(to_download))
        for file_info in to_download:
            logging.info("  %s (%s bytes)", file_info["name"], file_info.get("size"))
        return 0

    if not to_download:
        logging.info("Nothing to download: everything is up to date.")
        return 0

    # --- disk space ---------------------------------------------------------
    free, needed = check_disk_space(dir_zip, to_download)
    logging.info(
        "Disk free: %.1f GiB | needed: %.1f GiB",
        free / 2**30,
        needed / 2**30,
    )
    if needed > free:
        logging.error(
            "Not enough free disk space in %s (need %.1f GiB, have %.1f GiB). "
            "Free up space or download a subset with --only.",
            dir_zip,
            needed / 2**30,
            free / 2**30,
        )
        return 1

    # --- download -----------------------------------------------------------
    max_workers = max(1, min(int(max_workers), len(to_download)))
    # Small files first, then large ones (optionally with their own worker cap).
    small = [f for f in to_download if (f.get("size") or 0) < large_threshold]
    large = [f for f in to_download if (f.get("size") or 0) >= large_threshold]
    max_large = max(1, min(int(max_large), len(large))) if large else 0

    stop_event = threading.Event()
    lock = threading.Lock()
    counters = {"ok": 0, "failed": 0}

    total_bytes = sum(
        max(0, (f.get("size") or 0) - existing_bytes(dir_zip, f)) for f in to_download
    )
    show_progress = not args.no_progress
    progress = Progress(total_bytes) if show_progress and total_bytes > 0 else None
    progress_cb = progress.update if progress else None

    def run_batch(batch, workers):
        if not batch:
            return
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        futures = {
            executor.submit(
                download_with_retries,
                file_info["url"],
                dir_zip,
                request_timeout,
                attempts=attempts,
                backoff=backoff,
                auth=auth,
                expected_size=file_info.get("size"),
                resume=resume,
                verify_zip=verify_zip,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                user_agent=user_agent,
                progress_cb=progress_cb,
                show_progress_bar=show_progress,
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
                    logging.error("Unhandled error downloading %s: %s", file_info["name"], exc)
                    ok = False
                with lock:
                    if ok:
                        counters["ok"] += 1
                        metadata[file_info["name"]] = make_metadata_entry(file_info)
                    else:
                        counters["failed"] += 1
        except BaseException:
            # Abort promptly (e.g. Ctrl-C) but let workers stop and keep .part files.
            stop_event.set()
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True)

    start_time = time.time()
    logging.info("Starting downloads (%d small, %d large)...", len(small), len(large))
    try:
        run_batch(small, max_workers)
        run_batch(large, max_large or max_workers)
    except KeyboardInterrupt:
        logging.warning(
            "Interrupted by user; stopped new downloads. Partial files are kept for resume."
        )
        save_metadata(meta_file, metadata)
        return 130
    finally:
        if progress is not None:
            progress.close()

    save_metadata(meta_file, metadata)

    elapsed = time.time() - start_time
    logging.info(
        "Summary: %d downloaded, %d skipped, %d failed. Elapsed: %.1f s",
        counters["ok"],
        skipped,
        counters["failed"],
        elapsed,
    )
    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
