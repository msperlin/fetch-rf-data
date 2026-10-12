"""DuckDB helpers shared by the SQLite and parquet loaders."""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
from pathlib import Path

import duckdb
from tqdm import tqdm

from ..progress import BAR_FORMAT

log = logging.getLogger("rfbpy")

# pandas calls it "latin1"; DuckDB calls it "latin-1".
_ENCODING_ALIASES = {
    "latin1": "latin-1",
    "latin-1": "latin-1",
    "iso-8859-1": "latin-1",
    "iso8859-1": "latin-1",
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "utf-16": "utf-16",
}


def normalize_encoding(encoding):
    """Translate a pandas-style encoding name to what DuckDB expects."""

    return _ENCODING_ALIASES.get(str(encoding).lower(), str(encoding))


def sql_str(value):
    """Escape a string for use as a SQL literal."""

    return "'" + str(value).replace("'", "''") + "'"


def _csv_struct(columns):
    inner = ", ".join(f"{sql_str(col)}: 'VARCHAR'" for col in columns)
    return "{" + inner + "}"


def human_size(num_bytes):
    """Format bytes as a readable string (e.g. ``7.1 GB``)."""

    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


def read_csv_sql(source, columns, separator=";", encoding="latin-1", null_padding=False):
    """Build a ``read_csv(...)`` call with explicit options (no sniffer)."""

    if isinstance(source, (list, tuple)):
        reader = "[" + ", ".join(sql_str(path) for path in source) + "]"
    else:
        reader = sql_str(source)
    return (
        f"read_csv({reader}, delim={sql_str(separator)}, header=false, "
        f"encoding={sql_str(encoding)}, auto_detect=false, quote='\"', escape='\"', "
        f"null_padding={'true' if null_padding else 'false'}, "
        f"all_varchar=false, columns={_csv_struct(columns)})"
    )


def code_table_query(table, source, separator, encoding, null_padding, stamp=None):
    """SQL ``SELECT`` producing a code table from ``source``."""

    read = read_csv_sql(source, ["codigo", "descricao"], separator, encoding, null_padding)
    items = ['"codigo"', '"descricao"']
    if stamp is not None:
        items.append(f"{sql_str(stamp)} AS source_month")
    return f"SELECT {', '.join(items)}\nFROM {read}"


def big_table_query(table, source, separator, encoding, null_padding, stamp=None):
    """SQL ``SELECT`` producing a big table from ``source``."""

    read = read_csv_sql(source, table.columns, separator, encoding, null_padding)
    # na_filter=False keeps empty fields as '' (not NULL).
    items = [f'coalesce("{col}", \'\') AS "{col}"' for col in table.selected]
    items += [f"{expr} AS {sql_str(name)}" for name, expr in table.derived]
    if stamp is not None:
        items.append(f"{sql_str(stamp)} AS source_month")
    sql = f"SELECT {', '.join(items)}\nFROM {read}"
    if table.where:
        sql += f"\nWHERE {table.where}"
    return sql


_LATIN1_C1 = re.compile(rb"[\x80-\x9f]")


class _QueryProgress:
    """tqdm bar tracking a DuckDB query through ``query_progress()``."""

    def __init__(self, con, desc="", enabled=False, position=1):
        self._con = con
        self._stop = threading.Event()
        self._thread = None
        self._enabled = enabled
        self._bar = (
            tqdm(
                total=100,
                desc=desc,
                unit="%",
                leave=False,
                position=position,
                dynamic_ncols=True,
                bar_format=BAR_FORMAT,
            )
            if enabled
            else None
        )

    def __enter__(self):
        if self._bar is not None:
            self._thread = threading.Thread(target=self._poll, daemon=True)
            self._thread.start()
        return self

    def _poll(self):
        while not self._stop.wait(0.3):
            try:
                pct = self._con.query_progress()
            except Exception:  # noqa: BLE001 - progress is informational only
                pct = -1
            if pct is not None and pct >= 0 and self._bar is not None:
                self._bar.n = max(self._bar.n, min(pct, 100.0))
                self._bar.refresh()

    def __exit__(self, exc_type, *_exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._bar is not None:
            if exc_type is None:
                self._bar.n = self._bar.total
            self._bar.refresh()
            self._bar.close()


def sanitize_latin1_file(path, temp_directory=None, show_progress=False):
    """Copy ``path`` replacing ``0x80-0x9f`` (C1) bytes with spaces.

    DuckDB's latin-1 reader rejects those bytes ("File is not latin-1
    encoded").  They appear rarely, usually in dropped fields (e.g. fax).
    Returns the sanitized copy path, or ``path`` when nothing changed.
    """

    path = Path(path)
    temp_directory = Path(temp_directory) if temp_directory else Path(tempfile.gettempdir())
    temp_directory.mkdir(parents=True, exist_ok=True)

    tmp_path = temp_directory / (path.name + ".latin1-sanitized")
    total = path.stat().st_size
    changed = False
    bar = tqdm(
        total=total,
        unit="B",
        unit_scale=True,
        desc=f"  sanitizing {path.name}",
        leave=False,
        position=1,
        dynamic_ncols=True,
        bar_format=BAR_FORMAT,
        disable=not show_progress,
    )
    try:
        with open(path, "rb") as src, open(tmp_path, "wb") as dst:
            while chunk := src.read(8 * 1024 * 1024):
                fixed = _LATIN1_C1.sub(b" ", chunk)
                if fixed != chunk:
                    changed = True
                dst.write(fixed)
                bar.update(len(chunk))
    finally:
        bar.close()

    if not changed:
        tmp_path.unlink(missing_ok=True)
        return str(path)

    log.info("Sanitized %s (C1 bytes replaced) -> %s", path.name, tmp_path)
    return str(tmp_path)


def execute_csv(build_sql, paths, temp_directory, con, show_progress=False, desc=""):
    """Run ``build_sql(paths)``; retry after sanitizing if DuckDB rejects latin-1.

    Sanitized copies are created only when the direct read fails and are
    removed afterwards, so the normal path pays no extra cost.
    """

    try:
        with _QueryProgress(con, desc=desc, enabled=show_progress):
            result = con.execute(build_sql(paths))
            return _first_value(result)
    except duckdb.Error as exc:
        if "not latin-1 encoded" not in str(exc):
            raise

    log.warning(
        "DuckDB rejected a latin-1 file; sanitizing C1 control bytes and retrying"
    )
    created = []
    try:
        sanitized_paths = []
        for path in paths:
            sanitized = sanitize_latin1_file(path, temp_directory, show_progress)
            if sanitized != path:
                created.append(sanitized)
            sanitized_paths.append(sanitized)
        with _QueryProgress(con, desc=desc, enabled=show_progress):
            result = con.execute(build_sql(sanitized_paths))
            return _first_value(result)
    finally:
        for tmp in created:
            Path(tmp).unlink(missing_ok=True)


def _first_value(result):
    if result is None:
        return None
    row = result.fetchone()
    return row[0] if row else None


def connect(
    *,
    threads=None,
    memory_limit=None,
    temp_directory=None,
    preserve_insertion_order=False,
    show_progress=False,
    need_sqlite=False,
):
    """Open a DuckDB connection configured for a bulk load."""

    con = duckdb.connect()
    if need_sqlite:
        try:
            con.execute("LOAD sqlite;")
        except duckdb.Error:
            con.execute("INSTALL sqlite; LOAD sqlite;")

    if threads:
        con.execute(f"SET threads = {int(threads)}")
    if memory_limit:
        con.execute(f"SET memory_limit = {sql_str(memory_limit)}")
    if temp_directory:
        Path(temp_directory).mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory = {sql_str(temp_directory)}")
    if show_progress:
        # Enable progress computation but keep the native bar off (we draw ours).
        con.execute("SET enable_progress_bar = true")
        con.execute("SET progress_bar_time = 999999999")
    con.execute(
        f"SET preserve_insertion_order = {'true' if preserve_insertion_order else 'false'}"
    )
    return con


def table_steps(code_tables, big_tables):
    """Return the ordered ``(kind, table)`` load steps."""

    steps = [("code", table) for table in code_tables]
    steps += [("big", table) for table in big_tables]
    return steps


def iter_table_paths(dir_unzip, pattern):
    """Sorted list of source files in ``dir_unzip`` matching ``pattern``."""

    return sorted(str(p) for p in Path(dir_unzip).glob("*" + pattern))


def source_bytes(paths):
    return sum(Path(p).stat().st_size for p in paths)


def timed(label):
    """Tiny context manager returning elapsed seconds for logging."""

    class _Timer:
        def __enter__(self):
            self.start = time.time()
            return self

        def __exit__(self, *exc):
            self.elapsed = time.time() - self.start
            return False

    return _Timer()
