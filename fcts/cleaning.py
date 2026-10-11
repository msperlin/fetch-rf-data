import pandas as pd

def clean_df(df_in, type_table, cols_to_drop):

    if type_table == "estabelecimento":
        
        df_out=(
                df_in
                .loc[lambda x: x["situacao_cadastral"] != "08"]  # remove canceled
                .assign(
                    cnpj=lambda x: x["cnpj_basico"] + x["cnpj_ordem"] + x["cnpj_dv"]
                )
                .drop(
                    columns=cols_to_drop
                    )
            )
    elif type_table == "empresas":
        df_out=(
                df_in
                .drop(
                    columns=cols_to_drop
                    )
            )
    elif type_table == "socios":
        df_out=(
                df_in
                .drop(
                    columns=cols_to_drop
                    )
            )
    else:
        #error
        raise ValueError(f"Unknown type_table: {type_table}")

    return df_out