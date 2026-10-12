import sqlite3

import duckdb
import pytest

from rfbpy import RFBData
from rfbpy.loaders import sqlite as sqlite_loader
from rfbpy.tables import CNPJ_EXPR, BigTable, CodeTable


@pytest.fixture(scope="module", autouse=True)
def _require_sqlite_extension():
    # The sqlite extension is downloaded on first use; skip when offline.
    try:
        con = duckdb.connect()
        try:
            con.execute("LOAD sqlite;")
        except duckdb.Error:
            con.execute("INSTALL sqlite; LOAD sqlite;")
        con.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"DuckDB sqlite extension unavailable: {exc}")


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
    _write_csv(src / "Estabelecimentos1.ESTABELE", ["87654321;0001;00;;01;;;33"])
    _write_csv(src / "Empresas0.EMPRECSV", ["12345678;ACME LTDA;2062", "87654321;SOLO ME;2135"])
    _write_csv(src / "Socios0.SOCIOCSV", ["12345678;2;JOAO;***123456**"])
    _write_csv(src / "Cnaes.CNAECSV", ["0111301;Produção de arroz"])
    return src, tmp_path / "out.db"


CODE_TABLES = [CodeTable("cnae", ".CNAECSV")]


def _big_tables():
    return [
        BigTable(
            "estabelecimento", ".ESTABELE",
            ["cnpj_basico", "cnpj_ordem", "cnpj_dv", "nome_fantasia",
             "situacao_cadastral", "uf", "municipio", "telefone1"],
            ["cnpj_basico", "nome_fantasia", "situacao_cadastral", "uf", "municipio"],
            derived=[("cnpj", CNPJ_EXPR)],
            where="coalesce(situacao_cadastral, '') <> '08'",
            drop=["cnpj_ordem", "cnpj_dv", "telefone1"],
        ),
        BigTable(
            "empresas", ".EMPRECSV",
            ["cnpj_basico", "razao_social", "natureza_juridica"],
            ["cnpj_basico", "razao_social", "natureza_juridica"],
        ),
        BigTable(
            "socios", ".SOCIOCSV",
            ["cnpj_basico", "identificador_de_socio", "nome_socio", "cnpj_cpf_socio"],
            ["cnpj_basico", "identificador_de_socio", "cnpj_cpf_socio"],
            drop=["nome_socio"],
        ),
    ]


def test_schema_and_data(env):
    src, db = env
    sqlite_loader.load_tables(db, src, CODE_TABLES, _big_tables())
    con = sqlite3.connect(db)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(estabelecimento)")]
        assert cols == [
            "cnpj_basico", "nome_fantasia", "situacao_cadastral", "uf", "municipio", "cnpj",
        ]
        rows = con.execute(
            "SELECT cnpj_basico, nome_fantasia, situacao_cadastral, uf, municipio, cnpj "
            "FROM estabelecimento ORDER BY cnpj_basico, cnpj"
        ).fetchall()
        assert rows == [
            ("12345678", "ACME", "02", "SP", "3550308", "12345678000190"),
            ("87654321", "", "01", "", "", "87654321000100"),
        ]
        assert con.execute("SELECT COUNT(*) FROM empresas").fetchone()[0] == 2
        assert con.execute("SELECT * FROM socios").fetchall() == [("12345678", "2", "***123456**")]
        assert con.execute("SELECT * FROM cnae").fetchall() == [("0111301", "Produção de arroz")]
    finally:
        con.close()


def test_missing_pattern_is_skipped(env):
    src, db = env
    (src / "Socios0.SOCIOCSV").unlink()
    sqlite_loader.load_tables(db, src, CODE_TABLES, _big_tables())
    con = sqlite3.connect(db)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert "estabelecimento" in names
    assert "socios" not in names


def test_latin1_c1_bytes_are_sanitized(env):
    src, db = env
    _write_csv(
        src / "Estabelecimentos0.ESTABELE",
        ["12345678;0001;90;AC\x8fME;02;SP;3550308;11"],
    )
    sqlite_loader.load_tables(db, src, CODE_TABLES, _big_tables())
    con = sqlite3.connect(db)
    try:
        value = con.execute(
            "SELECT nome_fantasia FROM estabelecimento WHERE cnpj_basico='12345678'"
        ).fetchone()[0]
    finally:
        con.close()
    assert value == "AC ME"


def test_finalize_creates_indexes(env):
    src, db = env
    sqlite_loader.load_tables(db, src, CODE_TABLES, _big_tables())
    count = sqlite_loader.finalize(db, [], [], show_progress=False)
    assert count == 2
    con = sqlite3.connect(db)
    try:
        indexes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        con.close()
    assert "idx_estabelecimento_cnpj" in indexes
    assert "idx_cnae" in indexes


def test_remove_source_files(env):
    src, db = env
    big = _big_tables()
    sqlite_loader.load_tables(db, src, CODE_TABLES, big)
    sqlite_loader.remove_source_files(src, CODE_TABLES, big)
    assert not list(src.glob("*.ESTABELE"))
    assert not list(src.glob("*.CNAECSV"))


def test_rfbdata_to_sqlite_prints_report(tmp_path, capsys):
    rfb = RFBData(
        processing_dir=str(tmp_path), files_to_download=["Cnaes.zip"], show_progress=False
    )
    rfb.unzip_dir.mkdir(parents=True, exist_ok=True)
    (rfb.unzip_dir / "Cnaes.CNAECSV").write_bytes("0111301;X".encode("latin1"))

    result = rfb.to_sqlite()

    out = capsys.readouterr().out
    assert "Building SQLite database:" in out
    assert "RFB CNPJ — sqlite" in out
    assert "cnae" in out
    assert result.tables["cnae"] == 1
    assert result.files  # the database file size


def test_rfbdata_to_sqlite_stamps_source(tmp_path):
    rfb = RFBData(
        processing_dir=str(tmp_path), files_to_download=["Cnaes.zip"], show_progress=False
    )
    rfb.unzip_dir.mkdir(parents=True, exist_ok=True)
    (rfb.unzip_dir / "Cnaes.CNAECSV").write_bytes("0111301;X".encode("latin1"))
    result = rfb.to_sqlite(print_report=False, source_month="2026-09")

    con = sqlite3.connect(result.path)
    try:
        meta = dict(con.execute("SELECT key, value FROM rfb_metadata").fetchall())
    finally:
        con.close()
    assert meta["source_month"] == "2026-09"
    assert meta["rfbpy_version"]


def test_rfbdata_to_sqlite_stamp_column(tmp_path):
    rfb = RFBData(
        processing_dir=str(tmp_path), files_to_download=["Cnaes.zip"],
        show_progress=False, stamp_column=True,
    )
    rfb.unzip_dir.mkdir(parents=True, exist_ok=True)
    (rfb.unzip_dir / "Cnaes.CNAECSV").write_bytes("0111301;X".encode("latin1"))
    result = rfb.to_sqlite(print_report=False, source_month="2026-09")
    con = sqlite3.connect(result.path)
    try:
        assert con.execute("SELECT source_month FROM cnae").fetchone() == ("2026-09",)
    finally:
        con.close()


def test_rfbdata_to_sqlite_print_report_off(tmp_path, capsys):
    rfb = RFBData(
        processing_dir=str(tmp_path), files_to_download=["Cnaes.zip"], show_progress=False
    )
    rfb.unzip_dir.mkdir(parents=True, exist_ok=True)
    (rfb.unzip_dir / "Cnaes.CNAECSV").write_bytes("0111301;X".encode("latin1"))
    rfb.to_sqlite(print_report=False)
    out = capsys.readouterr().out
    assert "Building SQLite database:" not in out
    assert "RFB CNPJ — sqlite" not in out
