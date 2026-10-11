import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "03_load-to-sqlite.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("load_to_sqlite", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


load = _load_module()


def _colunas():
    cols = ["a", "b", "c"]
    return {
        "socios_cols": cols,
        "socios_cols_to_drop": [],
        "empresas_cols": cols,
        "empresas_cols_to_drop": [],
        "estabelecimento_cols": cols,
        "estabelecimento_cols_to_drop": [],
    }


class TestDownloadKey(unittest.TestCase):
    def test_strips_zip_and_numeric_suffix(self):
        self.assertEqual(load._download_key("Cnaes.zip"), "cnaes")
        self.assertEqual(load._download_key("Estabelecimentos0.zip"), "estabelecimentos")
        self.assertEqual(load._download_key("  Socios "), "socios")


class TestEnabledPatterns(unittest.TestCase):
    def test_selection_excludes_missing_tables(self):
        config = {"arquivos": {"files_to_download": ["Cnaes.zip", "Empresas", "Socios"]}}
        self.assertEqual(
            load.enabled_patterns(config),
            {".CNAECSV", ".EMPRECSV", ".SOCIOCSV"},
        )

    def test_codes_only(self):
        config = {"arquivos": {"files_to_download": ["Motivos.zip", "Municipios.zip"]}}
        self.assertEqual(load.enabled_patterns(config), {".MOTICSV", ".MUNICCSV"})

    def test_missing_list_means_all(self):
        self.assertIsNone(load.enabled_patterns({}))
        self.assertIsNone(load.enabled_patterns({"arquivos": {}}))


class TestTableSelection(unittest.TestCase):
    def setUp(self):
        self.config = {"colunas": _colunas()}

    def test_codes_filtered(self):
        patterns = {".CNAECSV", ".MOTICSV"}
        names = [t.name for t in load.build_code_tables(patterns)]
        self.assertEqual(names, ["cnae", "motivo"])

    def test_codes_unfiltered(self):
        names = [t.name for t in load.build_code_tables()]
        self.assertIn("cnae", names)
        self.assertIn("qualificacao", names)

    def test_big_tables_filtered(self):
        patterns = {".ESTABELE", ".EMPRECSV"}
        names = [t.name for t in load.build_big_tables(self.config, patterns)]
        self.assertEqual(names, ["empresas", "estabelecimento"])

    def test_big_tables_unfiltered_includes_socios(self):
        names = [t.name for t in load.build_big_tables(self.config)]
        self.assertIn("socios", names)


if __name__ == "__main__":
    unittest.main()
