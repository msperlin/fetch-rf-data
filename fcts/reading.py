import pandas as pd
import os
import sqlalchemy
import glob

from fcts.cleaning import clean_df
from fcts.utils import get_config

config = get_config()

# CSV layout of the Receita public data files (overridable via [leitura]).
_leitura = config.get("leitura", {})
CHUNKSIZE = _leitura.get("chunksize", 500_000)
ENCODING = _leitura.get("encoding", "latin1")
SEPARATOR = _leitura.get("separator", ";")

# load small lookup tables and index them
def load_code_table(file_ext, table_name, 
                    conn,
                    dir_unzip, 
                    remove_extracted_after_use=True):
    files = list(glob.glob(os.path.join(dir_unzip, "*" + file_ext)))
    if not files:
        print(f"No file found for {file_ext} to load table {table_name}")
        return
    file_path = files[0]
    print("loading file", os.path.basename(file_path), "into table", table_name)
    df = pd.read_csv(
        file_path,
        dtype=str,
        sep=SEPARATOR,
        encoding=ENCODING,
        header=None,
        names=["codigo", "descricao"],
    )

    df.to_sql(table_name, conn, if_exists="replace", index=False)

    conn.execute(f"CREATE INDEX idx_{table_name} ON {table_name}(codigo);")
    
    if remove_extracted_after_use:
        print("removing file", file_path)
        os.remove(file_path)

def create_table_sql(table_name, columns):
    sql = "CREATE TABLE " + table_name + " ("
    for k, col in enumerate(columns):
        sql += "\n" + col + " TEXT"
        if k + 1 < len(columns):
            sql += ","
    sql += "\n)\n"
    return sql


def load_sqlite_by_pattern(
        table_name, file_pattern, columns, cols_to_drop, 
        dir_unzip, engine_url,
        remove_extracted_after_use=True):
        
    files = sorted(list(glob.glob(os.path.join(dir_unzip, "*" + file_pattern))))

    for k, f in enumerate(files):
        print(f"** [{k+1}|{len(files)}] LOADING {os.path.basename(f)} into table {table_name} **")
    
        # estimate total chunks by counting file lines (may be approximate if fields contain newlines)
        chunksize = CHUNKSIZE
        total_rows = None
        try:
            with open(f, "r", encoding=ENCODING, errors="replace") as fh:
                total_rows = sum(1 for _ in fh)
        except Exception:
            total_rows = None

        csv_iter = pd.read_csv(
            f,
            sep=SEPARATOR,
            iterator=True,
            chunksize=chunksize,
            header=None,
            names=columns,
            encoding=ENCODING,
            dtype=str,
            engine="c",
            na_filter=False
        )

        total_chunks = None
        if total_rows is not None and total_rows > 0:
            total_chunks = (total_rows + chunksize - 1) // chunksize

        for i_chunk, chunk in enumerate(csv_iter):
            idx = i_chunk + 1
            total_info = f"/{total_chunks}" if total_chunks is not None else ""
            print(f"\t-> [{idx}{total_info}]  got {len(chunk)} rows, fixing columns and values ...")

            chunk = clean_df(chunk, table_name, cols_to_drop)

            print(f"\t-> dumping {table_name} chunk of size {len(chunk)} to sqlite")

            chunk.to_sql(
                table_name, engine_url, 
                index=None, if_exists="append", 
                dtype=sqlalchemy.sql.sqltypes.TEXT
                )

            #if i_chunk == 0:
            #    break # for testing, remove this line to process all chunks
        
        if remove_extracted_after_use:
            print("\t-> removing file", f)
            os.remove(f)

        #if k == 0:
        #    break # for testing, remove this line to process all files

        print("\t-> partial done...")

