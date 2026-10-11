import pandas as pd, sqlite3, sqlalchemy
from pathlib import Path
from datetime import date


sqlite_f = Path("/home/msperlin/Desktop/dados-publicos/cnpj.db")
path_parquet = Path("/home/msperlin/Desktop/cnpj-parquet-files")
date_str = date.today().strftime("%Y%m%d")

path_parquet.mkdir(exist_ok=True)
cols_dict = {
    "estabelecimento": ["cnpj", "situacao_cadastral", "data_inicio_atividades", "cnae_fiscal", 'municipio', "uf"],
    "socios": ["*"]
}

if not sqlite_f.exists():
    raise FileNotFoundError(f"cant find {sqlite_f}")

engine = sqlite3.connect(sqlite_f)

df_tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;", engine)

removed_tables = ["empresas", "simples"]

cnae = (
    pd.read_sql_query("select * from cnae", engine)
    .rename(columns={"descricao": "cnae_nome"})
)

municipio = (
    pd.read_sql_query("select * from municipio", engine)
    .rename(columns={"descricao": "municipio_nome"})
)

motivo = pd.read_sql_query("select * from motivo", engine)
natureza_juridica = pd.read_sql_query("select * from natureza_juridica", engine)
pais = pd.read_sql_query("select * from pais", engine)
qualificacao_socio = pd.read_sql_query("select * from qualificacao_socio", engine)

# get 'estabelecimento'
cols = cols_dict.get("estabelecimento")
my_q = f"SELECT {", ".join(cols)} FROM estabelecimento where situacao_cadastral = '02' limit 100000"
my_q = f"SELECT {", ".join(cols)} FROM estabelecimento where situacao_cadastral = '02'"

print(f"running query: {my_q}")
df_estab = pd.read_sql_query(
        my_q,
        engine
        )

df_estab = (
    df_estab
    .merge(cnae, left_on="cnae_fiscal", right_on="codigo")
    .drop(columns=["codigo"])
    .merge(municipio, left_on="municipio", right_on="codigo")
    .drop(columns=["codigo"])
    .assign(
        data_inicio_atividades = pd.to_datetime(df_estab["data_inicio_atividades"])
    )
)

df_estab["cnae_nome"].value_counts()

f_estab = path_parquet / f"{date_str}_estabelecimento.parquet"
df_estab.to_parquet(f_estab)


# get 'socios'
unique_cnpj = df_estab["cnpj"].unique()
del df_estab

cols = cols_dict.get("socios")
my_q = f"SELECT {', '.join(cols)} FROM socios"

print(f"running query: {my_q}")

df_socios = pd.read_sql_query(
        my_q,
        engine
        )

df_socios = (
    df_socios
    .loc[lambda x: x['cnpj'].isin(unique_cnpj), :]
)

f_socios = path_parquet / f"{date_str}_socios.parquet"
df_socios.to_parquet(f_socios)

engine.close()
