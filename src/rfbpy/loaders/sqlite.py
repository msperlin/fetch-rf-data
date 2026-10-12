"""Load the extracted RFB CSVs into a SQLite database using DuckDB."""

from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from tqdm import tqdm

from ..progress import BAR_FORMAT
from ..tables import INDEX_SPECS
from . import duck

log = logging.getLogger("rfbpy")


def apply_pragmas(dbapi_connection, pragmas) -> None:
    """Apply a sequence of PRAGMAs to a DBAPI connection."""

    cursor = dbapi_connection.cursor()
    try:
        for pragma in pragmas:
            cursor.execute(pragma)
    finally:
        cursor.close()


def load_tables(
    path_sqlite,
    dir_unzip,
    code_tables,
    big_tables,
    *,
    separator=";",
    encoding="latin1",
    threads=None,
    memory_limit=None,
    temp_directory=None,
    null_padding=False,
    stamp_value=None,
    show_progress=False,
):
    """Create ``path_sqlite`` with the code tables and the big tables.

    When ``stamp_value`` is given, a ``source_month`` column is added.

    Returns ``{table_name: row_count}``.
    """

    dir_unzip = Path(dir_unzip)
    encoding = duck.normalize_encoding(encoding)

    steps = duck.table_steps(code_tables, big_tables)
    total_steps = len(steps)
    rows_loaded: dict[str, int] = {}

    con = duck.connect(
        threads=threads,
        memory_limit=memory_limit,
        temp_directory=temp_directory,
        show_progress=show_progress,
        need_sqlite=True,
    )
    outer = tqdm(
        total=total_steps,
        desc="Tables",
        unit="table",
        leave=True,
        position=0,
        dynamic_ncols=True,
        bar_format=BAR_FORMAT,
        disable=not show_progress,
    )
    try:
        con.execute(f"ATTACH {duck.sql_str(path_sqlite)} AS out (TYPE sqlite)")

        for step, (kind, table) in enumerate(steps, start=1):
            paths = duck.iter_table_paths(dir_unzip, table.pattern)
            if not paths:
                log.warning(
                    "[%d/%d] %s: no file found (pattern %s); skipping",
                    step, total_steps, table.name, table.pattern,
                )
                outer.update(1)
                continue

            log.info(
                "[%d/%d] Loading %s '%s': %d file(s), %s",
                step, total_steps,
                "code table" if kind == "code" else "table",
                table.name, len(paths), duck.human_size(duck.source_bytes(paths)),
            )
            outer.set_postfix_str(table.name)
            started = time.time()

            if kind == "code":
                def build(paths, t=table):
                    query = duck.code_table_query(
                        t, paths, separator, encoding, null_padding, stamp=stamp_value
                    )
                    return f'CREATE TABLE out."{t.name}" AS\n{query}'
            else:
                def build(paths, t=table):
                    query = duck.big_table_query(
                        t, paths, separator, encoding, null_padding, stamp=stamp_value
                    )
                    return f'CREATE TABLE out."{t.name}" AS\n{query}'

            rows = duck.execute_csv(
                build, paths, temp_directory, con,
                show_progress=show_progress, desc=f"  {table.name}",
            )
            rows_loaded[table.name] = rows if isinstance(rows, int) else -1
            log.info(
                "[%d/%d] %s: %s row(s) in %.1f s",
                step, total_steps, table.name,
                f"{rows:,}" if isinstance(rows, int) else "?",
                time.time() - started,
            )
            outer.update(1)
            outer.set_postfix_str("")

        con.execute("DETACH out")
    finally:
        outer.close()
        con.close()

    return rows_loaded


def finalize(path_sqlite, load_pragmas, final_pragmas, show_progress=True):
    """Create indexes, run ``ANALYZE`` and restore durable PRAGMAs.

    Returns the number of establishments (or ``None`` when the table is
    missing).
    """

    conn = sqlite3.connect(path_sqlite)
    try:
        apply_pragmas(conn, load_pragmas)

        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

        specs = [spec for spec in INDEX_SPECS if spec[0] in existing]
        log.info(
            "Creating %d index(es) on tables: %s",
            len(specs),
            ", ".join(sorted({table for table, _, _ in specs})) or "(none)",
        )
        for table, column, index in tqdm(
            specs, desc="Indexes", unit="index",
            disable=not show_progress, dynamic_ncols=True, bar_format=BAR_FORMAT,
        ):
            started = time.time()
            conn.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {table} ({column})")
            log.info("  index %s on %s (%.1f s)", index, table, time.time() - started)
        conn.commit()

        log.info("Collecting statistics (ANALYZE)...")
        started = time.time()
        conn.executescript("ANALYZE;")
        log.info("  ANALYZE done in %.1f s", time.time() - started)

        for pragma in final_pragmas:
            conn.execute(pragma)
        conn.commit()

        if "estabelecimento" in existing:
            return conn.execute("SELECT COUNT(*) FROM estabelecimento").fetchone()[0]
        return None
    finally:
        conn.close()


def write_metadata(path_sqlite, mapping, table="rfb_metadata") -> None:
    """Create/replace a key/value metadata table in the SQLite database."""

    conn = sqlite3.connect(path_sqlite)
    try:
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" (key TEXT PRIMARY KEY, value TEXT)'
        )
        rows = []
        for key, value in mapping.items():
            if value is None:
                text = ""
            elif isinstance(value, (dict, list)):
                text = json.dumps(value, ensure_ascii=False)
            else:
                text = str(value)
            rows.append((str(key), text))
        conn.executemany(
            f'INSERT OR REPLACE INTO "{table}" (key, value) VALUES (?, ?)', rows
        )
        conn.commit()
    finally:
        conn.close()


def remove_source_files(dir_unzip, code_tables, big_tables) -> None:
    """Remove the source CSVs after a successful load."""

    patterns = [t.pattern for t in code_tables] + [t.pattern for t in big_tables]
    for pattern in patterns:
        for path in glob.glob(str(Path(dir_unzip) / ("*" + pattern))):
            log.info("Removing file: %s", path)
            os.remove(path)
