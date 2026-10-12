"""Load the extracted RFB CSVs into one parquet file per table (via DuckDB)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from tqdm import tqdm

from ..progress import BAR_FORMAT
from . import duck

log = logging.getLogger("rfbpy")


def load_tables(
    dir_parquet,
    dir_unzip,
    code_tables,
    big_tables,
    *,
    separator=";",
    encoding="latin1",
    compression="zstd",
    threads=None,
    memory_limit=None,
    temp_directory=None,
    null_padding=False,
    stamp_value=None,
    metadata=None,
    show_progress=False,
):
    """Write ``<dir_parquet>/<table>.parquet`` for every table.

    When ``stamp_value`` is given, a ``source_month`` column is added; when
    ``metadata`` (a mapping of str -> str) is given, it is embedded in each
    parquet file as key/value metadata.

    Returns ``{table_name: row_count}``.
    """

    dir_parquet = Path(dir_parquet)
    dir_parquet.mkdir(parents=True, exist_ok=True)
    dir_unzip = Path(dir_unzip)
    encoding = duck.normalize_encoding(encoding)
    kv = {str(k): str(v) for k, v in (metadata or {}).items() if v is not None}

    steps = duck.table_steps(code_tables, big_tables)
    total_steps = len(steps)
    rows_loaded: dict[str, int] = {}

    con = duck.connect(
        threads=threads,
        memory_limit=memory_limit,
        temp_directory=temp_directory,
        show_progress=show_progress,
        need_sqlite=False,
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
        for step, (kind, table) in enumerate(steps, start=1):
            paths = duck.iter_table_paths(dir_unzip, table.pattern)
            if not paths:
                log.warning(
                    "[%d/%d] %s: no file found (pattern %s); skipping",
                    step, total_steps, table.name, table.pattern,
                )
                outer.update(1)
                continue

            out_path = dir_parquet / f"{table.name}.parquet"
            log.info(
                "[%d/%d] Writing parquet %s '%s': %d file(s), %s",
                step, total_steps,
                "code table" if kind == "code" else "table",
                table.name, len(paths), duck.human_size(duck.source_bytes(paths)),
            )
            outer.set_postfix_str(table.name)
            started = time.time()

            if kind == "code":
                def build(paths, t=table, out=out_path):
                    query = duck.code_table_query(
                        t, paths, separator, encoding, null_padding, stamp=stamp_value
                    )
                    return _copy_sql(query, out, compression, kv)
            else:
                def build(paths, t=table, out=out_path):
                    query = duck.big_table_query(
                        t, paths, separator, encoding, null_padding, stamp=stamp_value
                    )
                    return _copy_sql(query, out, compression, kv)

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
    finally:
        outer.close()
        con.close()

    return rows_loaded


def _copy_sql(query, out_path, compression, metadata=None):
    path = str(out_path).replace("'", "''")
    comp = str(compression).upper()
    options = f"FORMAT PARQUET, COMPRESSION {comp}"
    if metadata:
        kv = ", ".join(f"{key}: {duck.sql_str(value)}" for key, value in metadata.items())
        options += f", KV_METADATA {{{kv}}}"
    return f"COPY (\n{query}\n) TO '{path}' ({options})"
