import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from fcts.duckload import BigTable, CodeTable, load_with_duckdb

CNPJ_EXPR = "coalesce(cnpj_basico, '') || coalesce(cnpj_ordem, '') || coalesce(cnpj_dv, '')"


def _write_csv(path, rows, encoding="latin1"):
    path.write_bytes("\n".join(rows).encode(encoding))


class TestDuckLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The sqlite extension is downloaded on first use; skip when offline.
        try:
            con = duckdb.connect()
            try:
                con.execute("LOAD sqlite;")
            except duckdb.Error:
                con.execute("INSTALL sqlite; LOAD sqlite;")
            con.close()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"DuckDB sqlite extension unavailable: {exc}")

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "src"
        self.src.mkdir()
        self.db = self.dir / "out.db"

        _write_csv(
            self.src / "Estabelecimentos0.ESTABELE",
            [
                "12345678;0001;90;ACME;02;SP;3550308;11",
                "12345678;0002;91;OLD;08;RJ;3304557;22",  # cancelada -> excluída
            ],
        )
        _write_csv(
            self.src / "Estabelecimentos1.ESTABELE",
            ["87654321;0001;00;;01;;;33"],  # campos vazios
        )
        _write_csv(
            self.src / "Empresas0.EMPRECSV",
            ["12345678;ACME LTDA;2062", "87654321;SOLO ME;2135"],
        )
        _write_csv(
            self.src / "Socios0.SOCIOCSV",
            ["12345678;2;JOAO;***123456**"],
        )
        _write_csv(self.src / "Cnaes.CNAECSV", ["0111301;Produção de arroz"])

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self):
        code_tables = [CodeTable("cnae", ".CNAECSV")]
        big_tables = [
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
        load_with_duckdb(self.db, self.src, code_tables, big_tables)

    def test_schema_and_data(self):
        self._load()
        con = sqlite3.connect(self.db)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(estabelecimento)")]
            self.assertEqual(
                cols,
                ["cnpj_basico", "nome_fantasia", "situacao_cadastral", "uf", "municipio", "cnpj"],
            )

            rows = con.execute(
                "SELECT cnpj_basico, nome_fantasia, situacao_cadastral, uf, municipio, cnpj "
                "FROM estabelecimento ORDER BY cnpj_basico, cnpj"
            ).fetchall()
            # cancelada (situacao 08) foi removida; vazios viraram ''
            self.assertEqual(
                rows,
                [
                    ("12345678", "ACME", "02", "SP", "3550308", "12345678000190"),
                    ("87654321", "", "01", "", "", "87654321000100"),
                ],
            )

            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM empresas").fetchone()[0], 2
            )
            self.assertEqual(
                con.execute("SELECT * FROM socios").fetchall(),
                [("12345678", "2", "***123456**")],
            )
            self.assertEqual(
                con.execute("SELECT * FROM cnae").fetchall(),
                [("0111301", "Produção de arroz")],
            )
        finally:
            con.close()

    def test_missing_pattern_is_skipped(self):
        # Nenhum arquivo .SOCIOCSV: a tabela não é criada, sem erro.
        (self.src / "Socios0.SOCIOCSV").unlink()
        self._load()
        con = sqlite3.connect(self.db)
        try:
            names = {
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            con.close()
        self.assertIn("estabelecimento", names)
        self.assertNotIn("socios", names)

    def test_latin1_c1_bytes_are_sanitized(self):
        # Um byte 0x8f (faixa C1) faz o DuckDB recusar o arquivo como latin-1;
        # a carga deve sanear o byte em vez de abortar.
        _write_csv(
            self.src / "Estabelecimentos0.ESTABELE",
            ["12345678;0001;90;AC\x8fME;02;SP;3550308;11"],
        )
        self._load()
        con = sqlite3.connect(self.db)
        try:
            value = con.execute(
                "SELECT nome_fantasia FROM estabelecimento WHERE cnpj_basico='12345678'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(value, "AC ME")


if __name__ == "__main__":
    unittest.main()
