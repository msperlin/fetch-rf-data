import duckdb
import pytest

from rfbpy.loaders import parquet as parquet_loader
from rfbpy.tables import CNPJ_EXPR, BigTable, CodeTable


def _write_csv(path, rows, encoding="latin1"):
    path.write_bytes("\n".join(rows).encode(encoding))


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_csv(
        src / "Estabelecimentos0.ESTABELE",
        [
            "12345678;0001;90;ACME;02;SP;3550308;11",
            "12345678;0002;91;OLD;08;RJ;3304557;22",  # cancelada -> excluída
        ],
    )
    _write_csv(src / "Cnaes.CNAECSV", ["0111301;Produção de arroz"])
    return src, tmp_path / "parquet"


CODE_TABLES = [CodeTable("cnae", ".CNAECSV")]

BIG_TABLES = [
    BigTable(
        "estabelecimento", ".ESTABELE",
        ["cnpj_basico", "cnpj_ordem", "cnpj_dv", "nome_fantasia",
         "situacao_cadastral", "uf", "municipio", "telefone1"],
        ["cnpj_basico", "nome_fantasia", "situacao_cadastral", "uf", "municipio"],
        derived=[("cnpj", CNPJ_EXPR)],
        where="coalesce(situacao_cadastral, '') <> '08'",
        drop=["cnpj_ordem", "cnpj_dv", "telefone1"],
    ),
]


def test_write_one_file_per_table(env):
    src, out = env
    rows = parquet_loader.load_tables(out, src, CODE_TABLES, BIG_TABLES)
    assert rows["cnae"] == 1
    assert rows["estabelecimento"] == 1
    assert (out / "cnae.parquet").exists()
    assert (out / "estabelecimento.parquet").exists()


def test_parquet_content_and_filtering(env):
    src, out = env
    parquet_loader.load_tables(out, src, CODE_TABLES, BIG_TABLES, compression="zstd")
    con = duckdb.connect()
    try:
        cnae = con.execute(
            f"SELECT codigo, descricao FROM read_parquet('{out}/cnae.parquet')"
        ).fetchall()
        assert cnae == [("0111301", "Produção de arroz")]

        est = con.execute(
            "SELECT cnpj_basico, nome_fantasia, cnpj "
            f"FROM read_parquet('{out}/estabelecimento.parquet')"
        ).fetchall()
        # cancelada (situacao 08) foi removida
        assert est == [("12345678", "ACME", "12345678000190")]
    finally:
        con.close()


def test_missing_pattern_is_skipped(env):
    src, out = env
    (src / "Cnaes.CNAECSV").unlink()
    rows = parquet_loader.load_tables(out, src, CODE_TABLES, BIG_TABLES)
    assert "cnae" not in rows
    assert not (out / "cnae.parquet").exists()


def test_latin1_c1_bytes_are_sanitized(env):
    src, out = env
    _write_csv(
        src / "Estabelecimentos0.ESTABELE",
        ["12345678;0001;90;AC\x8fME;02;SP;3550308;11"],
    )
    parquet_loader.load_tables(out, src, CODE_TABLES, BIG_TABLES)
    con = duckdb.connect()
    try:
        value = con.execute(
            f"SELECT nome_fantasia FROM read_parquet('{out}/estabelecimento.parquet')"
        ).fetchone()[0]
    finally:
        con.close()
    assert value == "AC ME"
