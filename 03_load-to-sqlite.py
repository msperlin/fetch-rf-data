"""Carrega os arquivos descompactados da Receita Federal em um banco SQLite.

Deve ser executado após 02_unzip-files.py. Cria as tabelas de códigos
(CNAE, motivos, municípios etc.), as tabelas grandes (estabelecimentos,
empresas e sócios) e os índices usados nas consultas.

Há dois backends de carga, selecionados por ``[sqlite].backend``:

- ``"duckdb"`` (padrão): lê os CSVs e escreve o SQLite com DuckDB. Muito mais
  rápido — lê todos os arquivos de cada padrão em paralelo e evita o
  ``pandas.to_sql``.
- ``"sqlite"``: rota legada com pandas + SQLAlchemy (``fcts.reading``).

As tabelas carregadas são somente as que têm origem em
``[arquivos].files_to_download``: o script de download pode ser configurado para
buscar apenas um subconjunto dos arquivos, e aqui só carregamos o que existe.

Opções relevantes no config.toml:

- ``[arquivos].files_to_download``: define quais tabelas serão carregadas.
- ``[leitura].remove_extracted_after_use``: apaga cada arquivo após carregá-lo.
- ``[sqlite].backend``: ``"duckdb"`` ou ``"sqlite"``.
- ``[sqlite].db_filename`` / ``db_date_format``: nome do banco de saída.
- ``[sqlite].load_pragmas`` / ``final_pragmas``: ajustes de performance.
- ``[sqlite].duckdb_*``: threads, memory_limit, temp_dir e null_padding.
- ``[sqlite].show_progress``: barras de progresso e mensagens (padrão ``true``).
"""

import glob
import logging
import os
import sqlite3
import time
from pathlib import Path

from sqlalchemy import create_engine, event
from tqdm import tqdm

from fcts.downloads import TqdmLoggingHandler
from fcts.duckload import BigTable, CodeTable, human_size, load_with_duckdb
from fcts.reading import create_table_sql, load_code_table, load_sqlite_by_pattern
from fcts.utils import get_config, get_dir_names, sqlite_filename

# PRAGMAs de carga em massa: o banco é criado do zero a cada execução, então
# podemos desligar a durabilidade/journal para acelerar as escritas/índices.
DEFAULT_LOAD_PRAGMAS = (
    "PRAGMA journal_mode = OFF",
    "PRAGMA synchronous = OFF",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -200000",  # ~200 MB de cache de páginas
)
DEFAULT_FINAL_PRAGMAS = (
    "PRAGMA journal_mode = DELETE",
    "PRAGMA synchronous = NORMAL",
)


def setup_logging():
    """Loga via ``tqdm.write`` para não quebrar as barras de progresso."""
    handler = TqdmLoggingHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# (sufixo do arquivo, nome da tabela) das tabelas pequenas de códigos.
CODE_TABLE_SPECS = (
    (".CNAECSV", "cnae"),
    (".MOTICSV", "motivo"),
    (".MUNICCSV", "municipio"),
    (".NATJUCSV", "natureza"),
    (".PAISCSV", "pais"),
    (".QUALSCSV", "qualificacao"),
)

# Mapeia cada item de ``[arquivos].files_to_download`` para o(s) padrão(ões) de
# arquivo extraído — e, portanto, para a(s) tabela(s) que ele produz. É o que
# permite carregar no banco apenas as tabelas cujos arquivos foram baixados.
FILE_TO_PATTERNS = {
    "cnaes": (".CNAECSV",),
    "estabelecimentos": (".ESTABELE",),
    "empresas": (".EMPRECSV",),
    "socios": (".SOCIOCSV",),
    "naturezas": (".NATJUCSV",),
    "municipios": (".MUNICCSV",),
    "paises": (".PAISCSV",),
    "qualificacoes": (".QUALSCSV",),
    "motivos": (".MOTICSV",),
}

# (tabela, coluna, nome do índice) para todas as tabelas criadas.
INDEX_SPECS = (
    ("estabelecimento", "cnpj", "idx_estabelecimento_cnpj"),
    ("empresas", "cnpj_basico", "idx_empresas_cnpj"),
    ("socios", "cnpj_basico", "idx_socios_cnpj"),
    ("cnae", "codigo", "idx_cnae"),
    ("motivo", "codigo", "idx_motivo"),
    ("municipio", "codigo", "idx_municipio"),
    ("natureza", "codigo", "idx_natureza"),
    ("pais", "codigo", "idx_pais"),
    ("qualificacao", "codigo", "idx_qualificacao"),
)


def apply_pragmas(dbapi_connection, pragmas):
    """Aplica uma sequência de PRAGMAs a uma conexão DBAPI."""
    cursor = dbapi_connection.cursor()
    try:
        for pragma in pragmas:
            cursor.execute(pragma)
    finally:
        cursor.close()


def kept_columns(columns, cols_to_drop):
    """Retorna ``columns`` sem as colunas de ``cols_to_drop``, mantendo a ordem."""
    cols_to_drop = set(cols_to_drop)
    return [col for col in columns if col not in cols_to_drop]


def build_big_tables(config, patterns=None):
    """Monta as especificações das tabelas grandes a partir de ``[colunas]``.

    Quando ``patterns`` é dado, mantém apenas as tabelas cujo padrão de arquivo
    está no conjunto (ou seja, as habilitadas em ``files_to_download``).
    """
    cols = config["colunas"]
    cnpj_expr = (
        "coalesce(cnpj_basico, '') || coalesce(cnpj_ordem, '') || coalesce(cnpj_dv, '')"
    )
    tables = [
        BigTable(
            "socios", ".SOCIOCSV",
            cols["socios_cols"],
            kept_columns(cols["socios_cols"], cols["socios_cols_to_drop"]),
            drop=cols["socios_cols_to_drop"],
        ),
        BigTable(
            "empresas", ".EMPRECSV",
            cols["empresas_cols"],
            kept_columns(cols["empresas_cols"], cols["empresas_cols_to_drop"]),
            drop=cols["empresas_cols_to_drop"],
        ),
        BigTable(
            "estabelecimento", ".ESTABELE",
            cols["estabelecimento_cols"],
            kept_columns(cols["estabelecimento_cols"], cols["estabelecimento_cols_to_drop"]),
            derived=[("cnpj", cnpj_expr)],
            # na_filter=False: campo vazio vira '', e '' != '08' (mantém a linha).
            where="coalesce(situacao_cadastral, '') <> '08'",
            drop=cols["estabelecimento_cols_to_drop"],
        ),
    ]
    if patterns is None:
        return tables
    return [table for table in tables if table.pattern in patterns]


def build_code_tables(patterns=None):
    """Especificações das tabelas de códigos, opcionalmente filtradas.

    ``patterns=None`` mantém todas; um conjunto restringe às habilitadas em
    ``files_to_download``.
    """
    return [
        CodeTable(name, pattern)
        for pattern, name in CODE_TABLE_SPECS
        if patterns is None or pattern in patterns
    ]


def _download_key(name):
    """Normaliza um item de ``files_to_download`` para a chave do mapeamento.

    Remove a extensão ``.zip`` e o sufixo numérico dos arquivos grandes
    (``Estabelecimentos0.zip`` -> ``estabelecimentos``).
    """
    stem = str(name).strip().lower()
    if stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return stem.rstrip("0123456789")


def enabled_patterns(config):
    """Padrões de arquivo habilitados por ``[arquivos].files_to_download``.

    Retorna ``None`` quando a lista não é definida (nesse caso carregamos todas
    as tabelas conhecidas, preservando o comportamento anterior).
    """
    files = config.get("arquivos", {}).get("files_to_download")
    if not files:
        return None

    patterns = set()
    for name in files:
        matched = FILE_TO_PATTERNS.get(_download_key(name))
        if matched:
            patterns.update(matched)
        else:
            logging.warning(
                "No table is mapped to %r in [arquivos].files_to_download; skipping it",
                name,
            )
    return patterns


def load_with_pandas(path_sqlite, config, dir_unzip, code_tables, big_tables,
                     remove_extracted, load_pragmas):
    """Backend legado: pandas + SQLAlchemy ``to_sql``."""

    conn = sqlite3.connect(path_sqlite)
    apply_pragmas(conn, load_pragmas)
    engine = create_engine(f"sqlite:///{path_sqlite}")
    event.listen(
        engine,
        "connect",
        lambda dbapi_connection, _record: apply_pragmas(dbapi_connection, load_pragmas),
    )
    try:
        for table in code_tables:
            load_code_table(table.pattern, table.name, conn, dir_unzip, remove_extracted)

        for table in big_tables:
            out_cols = list(table.selected) + [name for name, _ in table.derived]
            conn.execute(create_table_sql(table.name, out_cols))

        for table in big_tables:
            load_sqlite_by_pattern(
                table.name, table.pattern, table.columns, table.drop,
                dir_unzip, engine, remove_extracted,
            )
        engine.dispose()
    finally:
        conn.close()
        engine.dispose()


def remove_source_files(dir_unzip, code_tables, big_tables):
    """Remove os CSVs de origem depois de uma carga bem-sucedida."""
    patterns = [t.pattern for t in code_tables] + [t.pattern for t in big_tables]
    for pattern in patterns:
        for path in glob.glob(str(Path(dir_unzip) / ("*" + pattern))):
            logging.info("Removendo arquivo: %s", path)
            os.remove(path)


def finalize(path_sqlite, load_pragmas, final_pragmas, show_progress=True):
    """Cria índices, coleta estatísticas e devolve o banco ao modo durável."""
    conn = sqlite3.connect(path_sqlite)
    try:
        apply_pragmas(conn, load_pragmas)

        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

        specs = [spec for spec in INDEX_SPECS if spec[0] in existing]
        logging.info(
            "Criando %d índice(s) nas tabelas: %s",
            len(specs), ", ".join(sorted({table for table, _, _ in specs})) or "(nenhuma)",
        )
        for table, column, index in tqdm(
            specs, desc="Índices", unit="índice",
            disable=not show_progress, dynamic_ncols=True,
        ):
            started = time.time()
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index} ON {table} ({column})"
            )
            logging.info("  índice %s em %s (%.1f s)", index, table, time.time() - started)
        conn.commit()

        logging.info("Coletando estatísticas (ANALYZE)...")
        started = time.time()
        conn.executescript("ANALYZE;")
        logging.info("  ANALYZE concluído em %.1f s", time.time() - started)

        for pragma in final_pragmas:
            conn.execute(pragma)
        conn.commit()

        if "estabelecimento" in existing:
            return conn.execute("SELECT COUNT(*) FROM estabelecimento").fetchone()[0]
        return None
    finally:
        conn.close()


def _optional(value):
    """Trata ``0``/vazio/None como 'não definido'."""
    if value in (None, "", 0, "0"):
        return None
    return value


def main():
    start_time = time.time()

    config = get_config()
    leitura_cfg = config.get("leitura", {})
    sqlite_cfg = config.get("sqlite", {})

    setup_logging()

    backend = str(sqlite_cfg.get("backend", "duckdb")).lower()
    remove_extracted_after_use = bool(leitura_cfg.get("remove_extracted_after_use", False))
    load_pragmas = sqlite_cfg.get("load_pragmas", DEFAULT_LOAD_PRAGMAS)
    final_pragmas = sqlite_cfg.get("final_pragmas", DEFAULT_FINAL_PRAGMAS)
    separator = leitura_cfg.get("separator", ";")
    encoding = leitura_cfg.get("encoding", "latin1")
    show_progress = bool(sqlite_cfg.get("show_progress", True))

    my_dirs = get_dir_names(config)
    processing_dir = Path(config["dirs"]["processing_dir"]).expanduser()
    dir_unzip = processing_dir / my_dirs[1]
    dir_sqlite = processing_dir / my_dirs[2]

    dir_sqlite.mkdir(parents=True, exist_ok=True)
    dir_unzip.mkdir(parents=True, exist_ok=True)

    path_sqlite = dir_sqlite / sqlite_filename(config)
    if path_sqlite.exists():
        logging.warning("O arquivo %s já existe; removendo.", path_sqlite)
        path_sqlite.unlink()

    logging.info("Início: %s", time.asctime())
    logging.info("Diretório de entrada (CSVs): %s", dir_unzip)
    logging.info("Banco de saída: %s", path_sqlite)
    logging.info("Backend: %s | encoding: %s | separador: %r", backend, encoding, separator)

    patterns = enabled_patterns(config)
    code_tables = build_code_tables(patterns)
    big_tables = build_big_tables(config, patterns)

    if patterns is not None:
        loaded = [t.name for t in code_tables] + [t.name for t in big_tables]
        logging.info(
            "Tabelas habilitadas por [arquivos].files_to_download: %s",
            ", ".join(loaded) or "(nenhuma)",
        )

    load_started = time.time()
    if backend == "duckdb":
        temp_dir = sqlite_cfg.get("duckdb_temp_dir") or (processing_dir / "duckdb_tmp")
        load_with_duckdb(
            path_sqlite,
            dir_unzip,
            code_tables,
            big_tables,
            separator=separator,
            encoding=encoding,
            threads=_optional(sqlite_cfg.get("duckdb_threads")),
            memory_limit=_optional(sqlite_cfg.get("duckdb_memory_limit")),
            temp_directory=temp_dir,
            null_padding=bool(sqlite_cfg.get("duckdb_null_padding", False)),
            show_progress=show_progress,
        )
        if remove_extracted_after_use:
            remove_source_files(dir_unzip, code_tables, big_tables)
    elif backend == "sqlite":
        logging.warning("Usando o backend legado SQLite (pandas + SQLAlchemy); mais lento.")
        load_with_pandas(
            path_sqlite, config, dir_unzip, code_tables, big_tables,
            remove_extracted_after_use, load_pragmas,
        )
    else:
        raise SystemExit(
            f"Unknown [sqlite].backend: {backend!r} (use 'duckdb' or 'sqlite')"
        )
    load_elapsed = time.time() - load_started
    logging.info("Carga das tabelas concluída em %.1f s", load_elapsed)

    finalize_started = time.time()
    establishment_count = finalize(
        path_sqlite, load_pragmas, final_pragmas, show_progress=show_progress
    )
    finalize_elapsed = time.time() - finalize_started

    try:
        db_size = human_size(path_sqlite.stat().st_size)
    except OSError:
        db_size = "?"

    print("-" * 20)
    print(f"Banco criado: {path_sqlite} ({db_size})")
    if establishment_count is None:
        print("Nenhuma tabela estabelecimento (nenhum arquivo .ESTABELE encontrado).")
    else:
        print(f"Estabelecimentos (matrizes e filiais): {establishment_count:,}")

    total_elapsed = time.time() - start_time
    print(f"Carga:  {load_elapsed:.1f} s")
    print(f"Índices/ANALYZE: {finalize_elapsed:.1f} s")
    print(f"Tempo total: {total_elapsed:.1f} s")
    print("Fim:", time.asctime())


if __name__ == "__main__":
    main()
