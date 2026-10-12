"""RFBpy — download, extract and load the Receita Federal CNPJ open data."""

from .config import (
    Arquivos,
    Cache,
    Colunas,
    Dirs,
    Downloads,
    Leitura,
    Parquet,
    RFBConfig,
    Sqlite,
    Stamp,
    Unzip,
    Urls,
    build_config,
)
from .core import (
    CheckReport,
    DownloadResult,
    LoadResult,
    RFBData,
    RunResult,
)
from .errors import (
    ConfigError,
    DownloadError,
    RFBError,
    ShareAccessError,
    UnavailableFilesError,
)
from .extract import ExtractResult
from .metadata import FileInfo, SourceInfo

__version__ = "0.2.0"

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
    "Cache",
    "Stamp",
    "Colunas",
    "FileInfo",
    "SourceInfo",
    "CheckReport",
    "DownloadResult",
    "ExtractResult",
    "LoadResult",
    "RunResult",
    "RFBError",
    "ConfigError",
    "ShareAccessError",
    "DownloadError",
    "UnavailableFilesError",
    "__version__",
]
