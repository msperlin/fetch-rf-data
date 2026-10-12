"""Configuration model for :mod:`rfbpy`.

Every option that used to live in ``config.toml`` is represented here as a
dataclass field.  The public entry point is :func:`build_config`, which turns a
flat set of keyword arguments (one per option) into a :class:`RFBConfig`; the
grouped dataclasses are an internal detail so that callers rarely type them.

``RFBConfig.from_toml`` is kept as a convenience so an existing ``config.toml``
can still be used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Iterable

from .errors import ConfigError

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

log = logging.getLogger("rfbpy")

DEFAULT_BASE_URL = (
    "https://arquivos.receitafederal.gov.br/public.php/webdav/"
    "Dados/Cadastros/CNPJ/"
)
DEFAULT_SHARE_TOKEN = "gn672Ad4CF8N6TK"

DEFAULT_FILES_TO_DOWNLOAD = [
    "Cnaes.zip",
    "Estabelecimentos",
    "Empresas",
    "Naturezas.zip",
    "Municipios.zip",
    "Paises.zip",
    "Qualificacoes.zip",
    "Motivos.zip",
]

DEFAULT_LOAD_PRAGMAS = [
    "PRAGMA journal_mode = OFF",
    "PRAGMA synchronous = OFF",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -200000",
]

DEFAULT_FINAL_PRAGMAS = [
    "PRAGMA journal_mode = DELETE",
    "PRAGMA synchronous = NORMAL",
]

DEFAULT_ESTABELECIMENTO_COLS = [
    "cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial",
    "nome_fantasia", "situacao_cadastral", "data_situacao_cadastral",
    "motivo_situacao_cadastral", "nome_cidade_exterior", "pais",
    "data_inicio_atividades", "cnae_fiscal", "cnae_fiscal_secundaria",
    "tipo_logradouro", "logradouro", "numero", "complemento", "bairro",
    "cep", "uf", "municipio", "ddd1", "telefone1", "ddd2", "telefone2",
    "ddd_fax", "fax", "correio_eletronico", "situacao_especial",
    "data_situacao_especial",
]

DEFAULT_ESTABELECIMENTO_COLS_TO_DROP = [
    "cnpj_ordem", "cnpj_dv", "ddd1", "telefone1", "ddd2", "telefone2",
    "ddd_fax", "fax", "correio_eletronico", "situacao_especial",
    "data_situacao_especial",
]

DEFAULT_EMPRESAS_COLS = [
    "cnpj_basico", "razao_social", "natureza_juridica",
    "qualificacao_responsavel", "capital_social_str", "porte_empresa",
    "ente_federativo_responsavel",
]

DEFAULT_SOCIOS_COLS = [
    "cnpj_basico", "identificador_de_socio", "nome_socio", "cnpj_cpf_socio",
    "qualificacao_socio", "data_entrada_sociedade", "pais",
    "representante_legal", "nome_representante",
    "qualificacao_representante_legal", "faixa_etaria",
]

DEFAULT_SOCIOS_COLS_TO_DROP = [
    "nome_socio", "representante_legal", "nome_representante",
]


@dataclass
class Dirs:
    """Working directories under ``processing_dir`` (``[dirs]``)."""

    processing_dir: str = "~/rfb_processing"
    zip_dir: str = "01_zip-files"
    unzip_dir: str = "02_unzipped_files"
    sqlite_dir: str = "03_sqlite_file"
    parquet_dir: str = "04_parquet_files"

    @property
    def processing(self) -> Path:
        return Path(self.processing_dir).expanduser()

    def paths(self) -> dict[str, Path]:
        """Return every working directory as an absolute :class:`Path`."""

        base = self.processing
        return {
            "processing": base,
            "zip": base / self.zip_dir,
            "unzip": base / self.unzip_dir,
            "sqlite": base / self.sqlite_dir,
            "parquet": base / self.parquet_dir,
        }


@dataclass
class Urls:
    """WebDAV share coordinates (``[urls]``)."""

    base_url: str = DEFAULT_BASE_URL
    share_token: str | None = DEFAULT_SHARE_TOKEN
    month: str = "latest"


@dataclass
class Downloads:
    """Download behaviour (``[downloads]``)."""

    max_concurrent_downloads: int = 3
    request_timeout: float = 15
    connect_timeout: float | None = 10
    read_timeout: float | None = 60
    max_attempts: int = 3
    backoff: float = 2
    large_file_threshold_mb: float = 100
    max_large_downloads: int = 2
    resume: bool = True
    verify_zip: bool = True
    user_agent: str = "rfbpy/1.0"


@dataclass
class Arquivos:
    """Which files to fetch (``[arquivos]``)."""

    files_to_download: list[str] = field(
        default_factory=lambda: list(DEFAULT_FILES_TO_DOWNLOAD)
    )


@dataclass
class Unzip:
    """Extraction behaviour (``[unzip]``)."""

    skip_existing: bool = True
    remove_zip_after_extract: bool = False


@dataclass
class Leitura:
    """CSV reading options (``[leitura]``)."""

    remove_extracted_after_use: bool = False
    encoding: str = "latin1"
    separator: str = ";"


@dataclass
class Sqlite:
    """SQLite output options (``[sqlite]``)."""

    duckdb_threads: int = 0
    duckdb_memory_limit: str = "0"
    duckdb_temp_dir: str = ""
    duckdb_null_padding: bool = False
    show_progress: bool = True
    db_filename: str = "{date}_RF-CNPJ-database.db"
    db_date_format: str = "%Y%m%d"
    load_pragmas: list[str] = field(default_factory=lambda: list(DEFAULT_LOAD_PRAGMAS))
    final_pragmas: list[str] = field(default_factory=lambda: list(DEFAULT_FINAL_PRAGMAS))


@dataclass
class Parquet:
    """Parquet output options (``[parquet]``)."""

    compression: str = "zstd"


@dataclass
class Colunas:
    """Column layout of the RFB CSVs (``[colunas]``)."""

    estabelecimento_cols: list[str] = field(
        default_factory=lambda: list(DEFAULT_ESTABELECIMENTO_COLS)
    )
    estabelecimento_cols_to_drop: list[str] = field(
        default_factory=lambda: list(DEFAULT_ESTABELECIMENTO_COLS_TO_DROP)
    )
    empresas_cols: list[str] = field(default_factory=lambda: list(DEFAULT_EMPRESAS_COLS))
    empresas_cols_to_drop: list[str] = field(default_factory=list)
    socios_cols: list[str] = field(default_factory=lambda: list(DEFAULT_SOCIOS_COLS))
    socios_cols_to_drop: list[str] = field(
        default_factory=lambda: list(DEFAULT_SOCIOS_COLS_TO_DROP)
    )


@dataclass
class RFBConfig:
    """The complete, grouped configuration of an :class:`~rfbpy.RFBData`."""

    dirs: Dirs = field(default_factory=Dirs)
    urls: Urls = field(default_factory=Urls)
    downloads: Downloads = field(default_factory=Downloads)
    arquivos: Arquivos = field(default_factory=Arquivos)
    unzip: Unzip = field(default_factory=Unzip)
    leitura: Leitura = field(default_factory=Leitura)
    sqlite: Sqlite = field(default_factory=Sqlite)
    parquet: Parquet = field(default_factory=Parquet)
    colunas: Colunas = field(default_factory=Colunas)

    # -- construction helpers ------------------------------------------------

    @classmethod
    def from_toml(cls, path: str | Path) -> "RFBConfig":
        """Build a config from a ``config.toml`` file.

        Unknown keys (e.g. removed options from older configs) are ignored with
        a warning so existing files keep working.
        """

        path = Path(path).expanduser()
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RFBConfig":
        config = cls()
        for section, group in _GROUPS.items():
            values = data.get(section) or {}
            if not isinstance(values, dict):
                raise ConfigError(f"[{section}] must be a table in config.toml")
            valid = {f.name for f in fields(group)}
            accepted = {k: v for k, v in values.items() if k in valid}
            ignored = set(values) - set(accepted)
            if ignored:
                log.warning(
                    "Ignoring unsupported option(s) in [%s]: %s",
                    section, ", ".join(sorted(ignored)),
                )
            setattr(config, section, replace(getattr(config, section), **accepted))
        unknown = set(data) - set(_GROUPS)
        if unknown:
            log.warning(
                "Ignoring unsupported config section(s): %s",
                ", ".join(sorted(unknown)),
            )
        return config

    # -- validation ----------------------------------------------------------

    def validate(self, require_files: bool = True) -> "RFBConfig":
        """Raise :class:`ConfigError` when required values are missing."""

        problems = []
        if not str(self.dirs.processing_dir).strip():
            problems.append("missing processing_dir")
        if not str(self.urls.base_url).strip():
            problems.append("missing base_url")
        if require_files and not self.arquivos.files_to_download:
            problems.append("missing files_to_download")
        if problems:
            raise ConfigError("Invalid configuration: " + "; ".join(problems))
        return self


# Group name -> group dataclass, used for TOML/mapping round-trips.
_GROUPS = {
    "dirs": Dirs,
    "urls": Urls,
    "downloads": Downloads,
    "arquivos": Arquivos,
    "unzip": Unzip,
    "leitura": Leitura,
    "sqlite": Sqlite,
    "parquet": Parquet,
    "colunas": Colunas,
}

# Flat option name -> (group name, field name).  Attribute names are unique
# across groups, so a flat namespace maps cleanly onto the grouped dataclasses.
_FLAT_MAP: dict[str, tuple[str, str]] = {}
for _group_name, _group in _GROUPS.items():
    for _f in fields(_group):
        _FLAT_MAP[_f.name] = (_group_name, _f.name)
# Aliases so the flat kwargs read naturally.
_FLAT_MAP["parquet_compression"] = ("parquet", "compression")


def flat_options() -> list[str]:
    """Return every accepted flat keyword-argument name."""

    return sorted(_FLAT_MAP)


def build_config(**kwargs: Any) -> RFBConfig:
    """Build an :class:`RFBConfig` from flat keyword arguments.

    Group dataclass instances may also be passed by their section name, e.g.
    ``build_config(urls=Urls(month="2026-08"))``.
    """

    config = RFBConfig()
    flat: dict[str, dict[str, Any]] = {}
    groups: dict[str, Any] = {}

    for key, value in kwargs.items():
        if key in _GROUPS:
            if not isinstance(value, _GROUPS[key]):
                raise ConfigError(
                    f"{key!r} must be a {_GROUPS[key].__name__} instance"
                )
            groups[key] = value
            continue
        target = _FLAT_MAP.get(key)
        if target is None:
            raise ConfigError(
                f"Unknown option {key!r}. Valid options: {', '.join(flat_options())}"
            )
        section, attr = target
        flat.setdefault(section, {})[attr] = value

    for section, values in flat.items():
        setattr(config, section, replace(getattr(config, section), **values))
    for section, value in groups.items():
        setattr(config, section, value)
    return config


def override_config(config: RFBConfig, **kwargs: Any) -> RFBConfig:
    """Return a copy of ``config`` with flat overrides applied."""

    changes: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _GROUPS:
            changes[key] = value
            continue
        target = _FLAT_MAP.get(key)
        if target is None:
            raise ConfigError(f"Unknown option {key!r}")
        section, attr = target
        group = changes.get(section, getattr(config, section))
        changes[section] = replace(group, **{attr: value})
    return replace(config, **changes)


def normalize_files(files: Iterable[str] | None) -> list[str]:
    """Normalize a list of ``files_to_download`` entries (strip blanks)."""

    return [str(name).strip() for name in (files or []) if str(name).strip()]
