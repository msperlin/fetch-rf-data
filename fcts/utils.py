import os
import time
from pathlib import Path

import toml

# Project root (the folder that contains this package and config.toml)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Fallbacks used when config.toml omits the corresponding keys.
DEFAULT_DIR_NAMES = ("01_zip-files", "02_unzipped_files", "03_sqlite_file")
DIR_KEYS = ("zip_dir", "unzip_dir", "sqlite_dir")
DEFAULT_SQLITE_FILENAME = "{date}_RF-CNPJ-database.db"
DEFAULT_DB_DATE_FORMAT = "%Y%m%d"


def get_dir_names(config=None):
    """Names of the three working sub-folders under ``processing_dir``.

    Reads ``[dirs]`` (``zip_dir``, ``unzip_dir``, ``sqlite_dir``) from
    ``config``, falling back to the historical names.
    """

    if config is None:
        config = get_config()
    dirs = config.get("dirs", {})
    return [dirs.get(key, default) for key, default in zip(DIR_KEYS, DEFAULT_DIR_NAMES)]


def sqlite_filename(config, when=None):
    """Build the output SQLite file name from ``[sqlite]`` in ``config``.

    ``db_filename`` may contain a ``{date}`` placeholder replaced using
    ``db_date_format`` (a ``strftime`` pattern) applied to ``when`` (defaults
    to now).
    """

    sqlite = config.get("sqlite", {})
    name = sqlite.get("db_filename", DEFAULT_SQLITE_FILENAME)
    date_format = sqlite.get("db_date_format", DEFAULT_DB_DATE_FORMAT)
    when = when if when is not None else time.localtime()
    return name.replace("{date}", time.strftime(date_format, when))


def get_config(config_path=None):
    """Load ``config.toml``.

    Resolution order:

    1. an explicit ``config_path`` argument;
    2. the ``RFB_CONFIG`` environment variable;
    3. ``config.toml`` next to the project root.

    Using ``__file__`` (instead of ``__name__``) means the file is found no
    matter which directory the script is launched from.
    """

    if config_path is None:
        config_path = os.environ.get("RFB_CONFIG")
    if config_path is None:
        config_path = PROJECT_ROOT / "config.toml"

    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    return toml.load(config_path)
