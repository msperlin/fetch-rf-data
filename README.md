# RFBpy

**RFBpy** downloads, extracts and loads the open **CNPJ** (Brazilian company registry)
data published by the Receita Federal, into **SQLite** or a **parquet** folder.

It replaces the old set of numbered scripts with a single object — `RFBData` — that
owns all the error handling and the decision of what still needs to be downloaded.

- Discovers the monthly folders on the public Nextcloud/WebDAV share.
- Downloads only what is missing or out of date (size + ETag check), with retries,
  backoff and resumable `.part` files.
- Extracts the archives idempotently and guards against Zip Slip.
- Loads the CSVs into a SQLite database (via DuckDB) or into one parquet file per table.
- Works around the share's flaky load balancer and the occasional non-latin-1 bytes.

## Installation

```bash
pip install rfbpy
```

or, with [uv](https://github.com/astral-sh/uv):

```bash
uv add rfbpy
```

Requires Python 3.10+.

## Quick start

```python
from rfbpy import RFBData

rfb = RFBData(
    processing_dir="~/Desktop/rfb_processing",
    files_to_download=["Cnaes.zip", "Estabelecimentos", "Empresas"],
)

rfb.list_months()      # -> ["2026-07", "2026-08", ...]
rfb.check()            # report: available / up to date / to download / bytes needed

rfb.download()         # downloads only what is missing, with resume + retries
rfb.unzip()            # idempotent extraction
rfb.to_sqlite()        # -> LoadResult(path=.../20261012_RF-CNPJ-database.db)
rfb.to_parquet()       # -> LoadResult(path=.../04_parquet_files)
```

Or run the whole pipeline in one call:

```python
result = rfb.run(destination="parquet")   # or destination="sqlite"
print(result.download.downloaded)
print(result.load.tables)                 # {'cnae': 1, 'estabelecimento': 2500000, ...}
```

### Selecting files

```python
rfb.download(only="Cnaes,Paises")   # comma-separated substrings
rfb.download(only=["Empresas"])     # or a list
rfb.download(month="2026-08")       # a specific folder instead of "latest"
rfb.download(force=True)            # re-download everything
rfb.download(dry_run=True)          # just report what would be downloaded
```

### Reading the result

```python
report = rfb.check()
report.to_download    # list[FileInfo] still missing
report.up_to_date     # list[FileInfo] already present
report.needed_bytes   # bytes still to download
report.enough_disk    # needed_bytes <= free_bytes
```

## Configuration

Every option that used to live in `config.toml` can be passed straight to the
constructor:

```python
rfb = RFBData(
    processing_dir="~/Desktop/rfb_processing",
    zip_dir="01_zip-files",
    unzip_dir="02_unzipped_files",
    sqlite_dir="03_sqlite_file",
    parquet_dir="04_parquet_files",

    base_url="https://arquivos.receitafederal.gov.br/public.php/webdav/Dados/Cadastros/CNPJ/",
    share_token="gn672Ad4CF8N6TK",     # or set RFB_SHARE_TOKEN
    month="latest",

    files_to_download=["Cnaes.zip", "Estabelecimentos", "Empresas"],

    max_concurrent_downloads=3,
    request_timeout=15,
    connect_timeout=10,
    read_timeout=60,
    max_attempts=3,
    backoff=2,
    large_file_threshold_mb=100,
    max_large_downloads=2,
    resume=True,
    verify_zip=True,
    user_agent="rfbpy/1.0",

    skip_existing=True,
    remove_zip_after_extract=False,
    remove_extracted_after_use=False,
    encoding="latin1",
    separator=";",

    duckdb_threads=0,
    duckdb_memory_limit="0",
    duckdb_temp_dir="",
    duckdb_null_padding=False,
    show_progress=True,
    db_filename="{date}_RF-CNPJ-database.db",
    db_date_format="%Y%m%d",

    parquet_compression="zstd",
)
```

You can also use the grouped dataclasses (`dirs=`, `urls=`, `downloads=`,
`arquivos=`, `unzip=`, `leitura=`, `sqlite=`, `parquet=`, `colunas=`), pass a
whole `RFBConfig`, or load an existing file:

```python
rfb = RFBData.from_toml("examples/config.toml")
```

See [`examples/config.toml`](examples/config.toml) for the full template. Unknown
keys from older configs are ignored with a warning. `RFB_SHARE_TOKEN` overrides
`share_token`.

## Outputs

### SQLite

`to_sqlite()` writes a database with the tables:

| Table | Contents |
|---|---|
| `estabelecimento` | Each establishment (address, CNAE, situation…), with a derived `cnpj` |
| `empresas` | Company-level data |
| `socios` | Partners (when `Socios` is enabled) |
| `cnae`, `motivo`, `municipio`, `natureza`, `pais`, `qualificacao` | Lookup code tables |

Only the tables whose files are in `files_to_download` are created. Indexes are
created on `cnpj`/`cnpj_basico` and `ANALYZE` is run at the end. Canceled
establishments (`situacao_cadastral == "08"`) are dropped.

### Parquet

`to_parquet()` writes `<parquet_dir>/<table>.parquet` (Zstandard by default), one
file per table, with the same cleaning applied. Read them with DuckDB, pandas or
polars:

```python
import duckdb
duckdb.sql("SELECT uf, count(*) FROM 'parquet/*.parquet' GROUP BY 1").show()
```

## Command line

```bash
rfbpy --config examples/config.toml months
rfbpy --config examples/config.toml check
rfbpy --config examples/config.toml download --dry-run
rfbpy --config examples/config.toml download --jobs 6
rfbpy --config examples/config.toml unzip
rfbpy --config examples/config.toml load --dest parquet
rfbpy --config examples/config.toml run --dest sqlite
```

The CLI reads `--config`, then `$RFB_CONFIG`, then `./config.toml`.

## Notes

- **Flaky WebDAV.** The share sits behind a load balancer whose nodes sometimes
  answer `PROPFIND` with an empty `HTTP 200` (or reset the connection) instead of
  the expected `207 Multistatus`. Directory listing is retried until a valid
  response arrives (usually ~20 s).
- **Non-latin-1 bytes.** The RFB CSVs are latin-1. Rarely, big files contain C1
  control bytes (`0x80–0x9F`) that DuckDB's latin-1 reader rejects; a temporary
  sanitized copy is used and removed afterwards, leaving the originals untouched.
- **DuckDB SQLite extension.** `to_sqlite()` downloads the DuckDB `sqlite`
  extension on first use (needs internet once). `to_parquet()` does not.

## Development

```bash
uv sync --extra dev
.venv/bin/python -m pytest
```

The tests are offline: network access is mocked and DuckDB's SQLite extension
tests are skipped when it is unavailable.

## License

MIT — see [LICENSE](LICENSE). Original downloader idea from
[rictom/cnpj-sqlite](https://github.com/rictom/cnpj-sqlite).
