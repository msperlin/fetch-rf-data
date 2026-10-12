from rfbpy.config import Colunas
from rfbpy.tables import (
    build_big_tables,
    build_code_tables,
    download_key,
    enabled_patterns,
)


def _colunas():
    cols = ["a", "b", "c"]
    return Colunas(
        estabelecimento_cols=cols,
        estabelecimento_cols_to_drop=[],
        empresas_cols=cols,
        empresas_cols_to_drop=[],
        socios_cols=cols,
        socios_cols_to_drop=[],
    )


def test_download_key_strips_zip_and_numeric_suffix():
    assert download_key("Cnaes.zip") == "cnaes"
    assert download_key("Estabelecimentos0.zip") == "estabelecimentos"
    assert download_key("  Socios ") == "socios"


def test_enabled_patterns_selection():
    assert enabled_patterns(["Cnaes.zip", "Empresas", "Socios"]) == {
        ".CNAECSV", ".EMPRECSV", ".SOCIOCSV",
    }


def test_enabled_patterns_codes_only():
    assert enabled_patterns(["Motivos.zip", "Municipios.zip"]) == {".MOTICSV", ".MUNICCSV"}


def test_enabled_patterns_empty_means_all():
    assert enabled_patterns([]) is None
    assert enabled_patterns(None) is None


def test_code_tables_filtered():
    names = [t.name for t in build_code_tables({".CNAECSV", ".MOTICSV"})]
    assert names == ["cnae", "motivo"]


def test_code_tables_unfiltered():
    names = [t.name for t in build_code_tables()]
    assert "cnae" in names
    assert "qualificacao" in names


def test_big_tables_filtered():
    names = [t.name for t in build_big_tables(_colunas(), {".ESTABELE", ".EMPRECSV"})]
    assert names == ["empresas", "estabelecimento"]


def test_big_tables_unfiltered_includes_socios():
    names = [t.name for t in build_big_tables(_colunas())]
    assert "socios" in names
