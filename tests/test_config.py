import pytest

from rfbpy import Urls
from rfbpy.config import RFBConfig, build_config, flat_options, override_config
from rfbpy.errors import ConfigError


def test_build_flat_defaults():
    cfg = build_config()
    assert cfg.dirs.zip_dir == "01_zip-files"
    assert cfg.urls.month == "latest"
    assert cfg.downloads.max_concurrent_downloads == 3


def test_build_flat_overrides():
    cfg = build_config(
        processing_dir="~/x",
        month="2026-08",
        parquet_compression="gzip",
        show_progress=False,
    )
    assert cfg.dirs.processing_dir == "~/x"
    assert cfg.urls.month == "2026-08"
    assert cfg.parquet.compression == "gzip"
    assert cfg.sqlite.show_progress is False


def test_unknown_option_raises():
    with pytest.raises(ConfigError):
        build_config(nope=1)


def test_group_kwarg():
    cfg = build_config(urls=Urls(month="2026-01"))
    assert cfg.urls.month == "2026-01"


def test_group_kwarg_wrong_type():
    with pytest.raises(ConfigError):
        build_config(urls={"month": "2026-01"})


def test_override_config_returns_copy():
    cfg = build_config()
    cfg2 = override_config(cfg, month="2026-02")
    assert cfg2.urls.month == "2026-02"
    assert cfg.urls.month == "latest"


def test_validate_missing_required():
    with pytest.raises(ConfigError):
        build_config(processing_dir="").validate()
    with pytest.raises(ConfigError):
        build_config(base_url="").validate()
    with pytest.raises(ConfigError):
        build_config(files_to_download=[]).validate()


def test_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[dirs]\nprocessing_dir="~/a"\n'
        '[urls]\nbase_url="http://x/"\nmonth="2026-09"\n'
        '[arquivos]\nfiles_to_download=["Cnaes.zip"]\n'
    )
    cfg = RFBConfig.from_toml(path)
    assert cfg.dirs.processing_dir == "~/a"
    assert cfg.urls.month == "2026-09"
    assert cfg.arquivos.files_to_download == ["Cnaes.zip"]


def test_from_toml_rejects_unknown_option(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[leitura]\nchunksize=10\nseparator="|"\n')
    with pytest.raises(ConfigError):
        RFBConfig.from_toml(path)


def test_from_toml_rejects_unknown_section(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[unknown_section]\nx=1\n')
    with pytest.raises(ConfigError):
        RFBConfig.from_toml(path)


def test_from_toml_missing_file():
    with pytest.raises(ConfigError):
        RFBConfig.from_toml("/nonexistent/rfbpy-config.toml")


def test_flat_options_contains_key_names():
    opts = flat_options()
    assert "processing_dir" in opts
    assert "parquet_compression" in opts
    assert "files_to_download" in opts
