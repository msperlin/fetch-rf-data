"""RFBpy — download, extract and load the Receita Federal CNPJ open data."""

from .config import (
    Arquivos,
    Colunas,
    Dirs,
    Downloads,
    Leitura,
    Parquet,
    RFBConfig,
    Sqlite,
    Unzip,
    Urls,
    build_config,
)
from .core import (
    CheckReport,
    DownloadResult,
    FileInfo,
    LoadResult,
    RFBData,
    RunResult,
)
from .errors import ConfigError, DownloadError, RFBError, ShareAccessError
from .extract import ExtractResult

__version__ = "0.1.0"

__all__ = [
    "RFBData",
    "RFBConfig",
    "build_config",
    "Dirs",
    "Urls",
    "Downloads",
    "Arquivos",
    "Unzip",
    "Leitura",
    "Sqlite",
    "Parquet",
    "Colunas",
    "FileInfo",
    "CheckReport",
    "DownloadResult",
    "ExtractResult",
    "LoadResult",
    "RunResult",
    "RFBError",
    "ConfigError",
    "ShareAccessError",
    "DownloadError",
    "__version__",
]
