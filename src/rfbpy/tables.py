"""Specifications of the RFB tables produced by the loaders.

Maps each entry of ``files_to_download`` to the extracted-file pattern(s) it
produces, and builds the code/big table specifications from ``[colunas]``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("rfbpy")


@dataclass
class CodeTable:
    """Small lookup table (two columns: ``codigo`` and ``descricao``)."""

    name: str
    pattern: str  # suffix, e.g. ".CNAECSV"


@dataclass
class BigTable:
    """Large table derived from one or more CSV files.

    ``columns`` is the raw CSV order (used to name the read columns);
    ``selected`` are the kept columns, in output order; ``derived`` are extra
    SQL expressions (e.g. ``cnpj``); ``where`` is an optional filter.
    """

    name: str
    pattern: str  # suffix, e.g. ".ESTABELE"
    columns: list
    selected: list
    derived: list = field(default_factory=list)  # [(name, sql_expression)]
    where: str | None = None
    drop: list = field(default_factory=list)


# (file suffix, table name) of the small code tables.
CODE_TABLE_SPECS = (
    (".CNAECSV", "cnae"),
    (".MOTICSV", "motivo"),
    (".MUNICCSV", "municipio"),
    (".NATJUCSV", "natureza"),
    (".PAISCSV", "pais"),
    (".QUALSCSV", "qualificacao"),
)

# Maps each ``files_to_download`` item to the extracted-file pattern(s) it
# produces — and therefore to the table(s) it feeds.
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

# (table, column, index name) for every created table.
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

CNPJ_EXPR = (
    "coalesce(cnpj_basico, '') || coalesce(cnpj_ordem, '') || coalesce(cnpj_dv, '')"
)


def kept_columns(columns, cols_to_drop):
    """Return ``columns`` without ``cols_to_drop``, preserving order."""

    cols_to_drop = set(cols_to_drop)
    return [col for col in columns if col not in cols_to_drop]


def build_code_tables(patterns=None):
    """Code table specs, optionally filtered to ``patterns``."""

    return [
        CodeTable(name, pattern)
        for pattern, name in CODE_TABLE_SPECS
        if patterns is None or pattern in patterns
    ]


def build_big_tables(colunas, patterns=None):
    """Build the big-table specs from a :class:`~rfbpy.config.Colunas`.

    When ``patterns`` is given, keep only the tables whose file pattern is in
    the set (i.e. those enabled in ``files_to_download``).
    """

    tables = [
        BigTable(
            "socios", ".SOCIOCSV",
            colunas.socios_cols,
            kept_columns(colunas.socios_cols, colunas.socios_cols_to_drop),
            drop=colunas.socios_cols_to_drop,
        ),
        BigTable(
            "empresas", ".EMPRECSV",
            colunas.empresas_cols,
            kept_columns(colunas.empresas_cols, colunas.empresas_cols_to_drop),
            drop=colunas.empresas_cols_to_drop,
        ),
        BigTable(
            "estabelecimento", ".ESTABELE",
            colunas.estabelecimento_cols,
            kept_columns(
                colunas.estabelecimento_cols,
                colunas.estabelecimento_cols_to_drop,
            ),
            derived=[("cnpj", CNPJ_EXPR)],
            # na_filter=False equivalent: empty field becomes '' (not NULL).
            where="coalesce(situacao_cadastral, '') <> '08'",
            drop=colunas.estabelecimento_cols_to_drop,
        ),
    ]
    if patterns is None:
        return tables
    return [table for table in tables if table.pattern in patterns]


def download_key(name):
    """Normalize a ``files_to_download`` item to a mapping key.

    Strips the ``.zip`` extension and the numeric suffix of the big files
    (``Estabelecimentos0.zip`` -> ``estabelecimentos``).
    """

    stem = str(name).strip().lower()
    if stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    return stem.rstrip("0123456789")


def enabled_patterns(files_to_download):
    """File patterns enabled by ``files_to_download``.

    Returns ``None`` when the list is empty (load every known table).
    """

    if not files_to_download:
        return None

    patterns = set()
    for name in files_to_download:
        matched = FILE_TO_PATTERNS.get(download_key(name))
        if matched:
            patterns.update(matched)
        else:
            log.warning(
                "No table is mapped to %r in files_to_download; skipping it", name
            )
    return patterns
