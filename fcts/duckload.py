"""Carrega os CSVs da Receita Federal em SQLite usando DuckDB.

DuckDB faz o trabalho pesado (ler todos os arquivos de um padrão glob em
paralelo, filtrar, derivar colunas) e escreve o resultado direto no arquivo
SQLite através da extensão ``sqlite``. Substitui a rota antiga baseada em
pandas ``to_sql``, que é muito mais lenta.

O backend legado (pandas + SQLAlchemy) continua disponível em
``fcts.reading`` para quando ``[sqlite].backend = "sqlite"``.
"""

import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
from tqdm import tqdm


@dataclass
class CodeTable:
    """Tabela pequena de códigos (duas colunas: ``codigo`` e ``descricao``)."""

    name: str
    pattern: str  # sufixo, ex.: ".CNAECSV"


@dataclass
class BigTable:
    """Tabela grande derivada de um ou mais arquivos CSV.

    ``columns`` é a ordem bruta do CSV (usada para nomear as colunas lidas);
    ``selected`` são as colunas mantidas, na ordem de saída; ``derived`` são
    expressões SQL extras (ex.: ``cnpj``); ``where`` é um filtro opcional.
    """

    name: str
    pattern: str  # sufixo, ex.: ".ESTABELE"
    columns: list
    selected: list
    derived: list = field(default_factory=list)  # [(nome, expressao_sql)]
    where: str | None = None
    # Colunas a descartar; usado apenas pelo backend legado (pandas).
    drop: list = field(default_factory=list)


# pandas usa "latin1"; o DuckDB chama isso de "latin-1".
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
    """Traduz o nome de encoding do pandas para o esperado pelo DuckDB."""

    return _ENCODING_ALIASES.get(str(encoding).lower(), str(encoding))


def _sql_str(value):
    """Escapa uma string para uso literal em SQL."""

    return "'" + str(value).replace("'", "''") + "'"


def _csv_struct(columns):
    inner = ", ".join(f"{_sql_str(col)}: 'VARCHAR'" for col in columns)
    return "{" + inner + "}"


def read_csv_sql(source, columns, separator=";", encoding="latin-1", null_padding=False):
    """Monta a chamada ``read_csv(...)`` com opções explícitas (sem sniffer).

    ``source`` pode ser um caminho/glob (``str``) ou uma lista de caminhos.
    A lista é usada quando é preciso substituir um arquivo problemático por uma
    cópia saneada antes de tentar ler de novo.
    """
    if isinstance(source, (list, tuple)):
        reader = "[" + ", ".join(_sql_str(path) for path in source) + "]"
    else:
        reader = _sql_str(source)
    return (
        f"read_csv({reader}, delim={_sql_str(separator)}, header=false, "
        f"encoding={_sql_str(encoding)}, auto_detect=false, quote='\"', escape='\"', "
        f"null_padding={'true' if null_padding else 'false'}, "
        f"all_varchar=false, columns={_csv_struct(columns)})"
    )


def _code_table_sql(table, source, separator, encoding, null_padding):
    read = read_csv_sql(source, ["codigo", "descricao"], separator, encoding, null_padding)
    return (
        f'CREATE TABLE out."{table.name}" AS\n'
        f'SELECT "codigo", "descricao"\n'
        f"FROM {read}"
    )


def _big_table_sql(table, source, separator, encoding, null_padding):
    read = read_csv_sql(source, table.columns, separator, encoding, null_padding)
    # na_filter=False do pandas guarda campo vazio como '' (e não NULL).
    items = [f'coalesce("{col}", \'\') AS "{col}"' for col in table.selected]
    items += [f"{expr} AS {_sql_str(name)}" for name, expr in table.derived]
    sql = (
        f'CREATE TABLE out."{table.name}" AS\n'
        f"SELECT {', '.join(items)}\n"
        f"FROM {read}"
    )
    if table.where:
        sql += f"\nWHERE {table.where}"
    return sql


_LATIN1_C1 = re.compile(rb"[\x80-\x9f]")


def human_size(num_bytes):
    """Formata bytes em uma string legível (ex.: ``7.1 GB``)."""

    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


class _QueryProgress:
    """Barra tqdm que acompanha uma query do DuckDB via ``query_progress()``.

    O DuckDB só calcula o progresso quando ``enable_progress_bar`` está ligado;
    configuramos ``progress_bar_time`` alto para que ele *não* desenhe a barra
    nativa e a desenhamos nós mesmos, integrada às demais mensagens.
    """

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
                bar_format="{l_bar}{bar:30}{r_bar}",
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
            except Exception:  # noqa: BLE001 - progresso é apenas informativo
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
    """Copia ``path`` trocando bytes ``0x80-0x9f`` (faixa C1) por espaço.

    O leitor latin-1 do DuckDB rejeita esses bytes ("File is not latin-1
    encoded"). Eles aparecem raramente nos CSVs da Receita, geralmente em
    campos descartados (ex.: fax). Retorna o caminho do arquivo saneado em
    ``temp_directory`` ou o próprio ``path`` quando nada precisa ser trocado.
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
        desc=f"  saneando {path.name}",
        leave=False,
        position=1,
        dynamic_ncols=True,
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

    logging.info("Sanitized %s (C1 bytes replaced) -> %s", path.name, tmp_path)
    return str(tmp_path)


def _execute_csv_table(con, build_sql, paths, temp_directory,
                       show_progress=False, desc=""):
    """Executa ``build_sql(paths)``; se o DuckDB recusar o latin-1, saneia e repete.

    Os arquivos saneados só são criados quando a leitura direta falha e são
    removidos ao final, de forma que a carga normal (sem bytes C1) não paga
    nenhum custo extra. Retorna o número de linhas carregadas.
    """
    try:
        with _QueryProgress(con, desc=desc, enabled=show_progress):
            result = con.execute(build_sql(paths))
            return result.fetchone()[0] if result is not None else None
    except duckdb.Error as exc:
        if "not latin-1 encoded" not in str(exc):
            raise

    logging.warning(
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
            return result.fetchone()[0] if result is not None else None
    finally:
        for tmp in created:
            Path(tmp).unlink(missing_ok=True)


def _connect(*, threads=None, memory_limit=None, temp_directory=None,
             preserve_insertion_order=False, show_progress=False):
    con = duckdb.connect()
    try:
        con.execute("LOAD sqlite;")
    except duckdb.Error:
        con.execute("INSTALL sqlite; LOAD sqlite;")

    if threads:
        con.execute(f"SET threads = {int(threads)}")
    if memory_limit:
        con.execute(f"SET memory_limit = {_sql_str(memory_limit)}")
    if temp_directory:
        Path(temp_directory).mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory = {_sql_str(temp_directory)}")
    if show_progress:
        # Habilita o cálculo de progresso; a barra nativa fica desligada
        # (progress_bar_time alto) porque desenhamos a nossa própria barra.
        con.execute("SET enable_progress_bar = true")
        con.execute("SET progress_bar_time = 999999999")
    con.execute(
        f"SET preserve_insertion_order = {'true' if preserve_insertion_order else 'false'}"
    )
    return con


def load_with_duckdb(
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
    show_progress=False,
):
    """Cria ``path_sqlite`` com as tabelas de códigos e as tabelas grandes.

    Quando ``show_progress`` é verdadeiro, desenha uma barra com o total de
    tabelas e, para cada tabela, uma segunda barra com o progresso da query do
    DuckDB (percentual) e mensagens de início/fim.
    """

    dir_unzip = Path(dir_unzip)
    encoding = normalize_encoding(encoding)

    steps = [("code", table) for table in code_tables]
    steps += [("big", table) for table in big_tables]
    total_steps = len(steps)

    con = _connect(
        threads=threads,
        memory_limit=memory_limit,
        temp_directory=temp_directory,
        show_progress=show_progress,
    )
    outer = tqdm(
        total=total_steps,
        desc="Tabelas",
        unit="tabela",
        leave=True,
        position=0,
        dynamic_ncols=True,
        disable=not show_progress,
    )
    try:
        con.execute(f"ATTACH {_sql_str(path_sqlite)} AS out (TYPE sqlite)")

        for step, (kind, table) in enumerate(steps, start=1):
            paths = sorted(str(p) for p in dir_unzip.glob("*" + table.pattern))
            if not paths:
                logging.warning(
                    "[%d/%d] %s: nenhum arquivo encontrado (padrão %s); pulando",
                    step, total_steps, table.name, table.pattern,
                )
                outer.update(1)
                continue

            total_bytes = sum(os.path.getsize(p) for p in paths)
            logging.info(
                "[%d/%d] Carregando %s '%s': %d arquivo(s), %s",
                step, total_steps,
                "tabela de códigos" if kind == "code" else "tabela",
                table.name, len(paths), human_size(total_bytes),
            )
            outer.set_postfix_str(table.name)
            started = time.time()
            build = (
                (lambda paths, t=table: _code_table_sql(t, paths, separator, encoding, null_padding))
                if kind == "code"
                else (lambda paths, t=table: _big_table_sql(t, paths, separator, encoding, null_padding))
            )
            rows = _execute_csv_table(
                con, build, paths, temp_directory,
                show_progress=show_progress, desc=f"  {table.name}",
            )
            elapsed = time.time() - started
            rows_txt = f"{rows:,}" if isinstance(rows, int) else "?"
            logging.info(
                "[%d/%d] %s: %s linha(s) em %.1f s",
                step, total_steps, table.name, rows_txt, elapsed,
            )
            outer.update(1)
            outer.set_postfix_str("")

        con.execute("DETACH out")
    finally:
        outer.close()
        con.close()
