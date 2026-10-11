# Processamento de Dados Públicos de CNPJ do Brasil

Este projeto fornece um conjunto de scripts Python para baixar, descompactar, limpar e carregar dados públicos de empresas brasileiras (CNPJ) da Receita Federal em um banco de dados SQLite.

## Funcionalidades

- Baixa os arquivos de dados CNPJ mais recentes do site oficial da Receita Federal.
- Descompacta os arquivos baixados.
- Limpa e processa os dados.
- Carrega os dados em um banco de dados SQLite.
- Cria índices para consultas mais rápidas.
- Fornece exemplos de consultas SQL.
- Configuração através de um arquivo `config.toml`.

## Pré-requisitos

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)

## Instalação com uv

1. Instale o `uv` em seu sistema. Instruções podem ser encontradas [aqui](https://github.com/astral-sh/uv#installation).

2. Clone este repositório:
   ```bash
   git clone https://github.com/msperlin/fetch-rf-data.git
   ```
3. Navegue até o diretório do projeto:
   ```bash
   cd fetch-rf-data
   ```
4. Crie o ambiente virtual e instale as dependências:
   ```bash
   uv sync
   ```
5. Ative o ambiente virtual
   ```bash
   source .venv/bin/activate
   ```
   
Opcionalmente, para rodar os comandos python diretamente, use `uv run`:

   ```bash
   uv run python 01_download-zip-files.py
   ```
   
## Configuração

O projeto é configurado através do arquivo `config.toml`. Aqui estão as principais configurações:

- **[dirs]**:
  - `processing_dir`: O diretório principal para armazenar os arquivos baixados, descompactados e o banco de dados.
  - `zip_dir`, `unzip_dir`, `sqlite_dir`: Nomes das subpastas de trabalho criadas dentro de `processing_dir`.

- **[urls]**:
  - `base_url`: A URL WebDAV da pasta do compartilhamento público do CNPJ na Receita Federal.
  - `share_token`: O token do compartilhamento público (necessário para acesso WebDAV). Pode ser sobrescrito pela variável de ambiente `RFB_SHARE_TOKEN`.
  - `month`: `"latest"` (pasta mais recente) ou uma pasta específica, por exemplo `"2026-08"`.

- **[downloads]**:
  - `max_concurrent_downloads`: O número de downloads simultâneos.
  - `request_timeout`: O tempo limite geral das requisições (usado quando os específicos não são definidos).
  - `connect_timeout` / `read_timeout`: Timeouts separados de conexão e leitura por chunk (opcionais).
  - `max_attempts`: Número de tentativas por arquivo.
  - `backoff`: Base do backoff exponencial (com jitter) entre tentativas.
  - `large_file_threshold_mb`: Arquivos acima deste tamanho formam uma segunda fase, com pool próprio.
  - `max_large_downloads`: Downloads simultâneos para os arquivos grandes.
  - `resume`: Retoma downloads parciais (`.part`) via HTTP Range.
  - `verify_zip`: Confere se o arquivo baixado é um zip válido.
  - `user_agent`: User-Agent enviado nas requisições WebDAV.

- **[arquivos]**:
  - `files_to_download`: Uma lista de arquivos para baixar. Também define quais
    tabelas o script `03_load-to-sqlite.py` carrega: apenas as que têm origem em
    um item desta lista (ex.: sem `Socios`, a tabela `socios` não é criada).

- **[unzip]**:
  - `skip_existing`: Pula arquivos `.zip` cujos membros já foram extraídos (extração idempotente).
  - `remove_zip_after_extract`: Apaga o arquivo `.zip` depois de extrair com sucesso.

- **[leitura]**:
  - `chunksize`: O número de linhas a serem lidas dos arquivos CSV por vez.
  - `remove_extracted_after_use`: Remove os arquivos extraídos depois de carregá-los no banco.
  - `encoding`, `separator`: Codificação e separador dos CSVs publicados pela Receita Federal.

- **[sqlite]**:
  - `backend`: `"duckdb"` (padrão, rápido) ou `"sqlite"` (legado com pandas + SQLAlchemy).
  - `db_filename`, `db_date_format`: Nome do banco de saída (o `{date}` é substituído pela data de execução).
  - `load_pragmas`, `final_pragmas`: PRAGMAs de performance aplicados durante e ao final da carga.
  - `duckdb_threads`, `duckdb_memory_limit`, `duckdb_temp_dir`, `duckdb_null_padding`: ajustes do DuckDB (usados quando `backend = "duckdb"`).
  - `show_progress`: exibe barras de progresso (tabelas, percentual de cada tabela e índices) e mensagens durante a carga. Padrão: `true`.

- **[colunas]**:
  - `*_cols`: Os nomes das colunas para cada tipo de dado.
  - `*_cols_to_drop`: As colunas a serem removidas durante o processo de limpeza.

## Uso

Os scripts são projetados para serem executados em uma ordem específica:

1. **Baixar os dados:**
   ```bash
   python 01_download-zip-files.py
   ```
   Este script irá baixar os arquivos especificados no `config.toml` para o diretório definido em `processing_dir`.

   Opções úteis:
   ```bash
   python 01_download-zip-files.py --list-months          # lista as pastas mensais disponíveis
   python 01_download-zip-files.py --dry-run              # mostra o que seria baixado
   python 01_download-zip-files.py --only Cnaes,Paises    # baixa apenas alguns arquivos
   python 01_download-zip-files.py --month 2026-08        # escolhe uma pasta mensal específica
   python 01_download-zip-files.py --jobs 6 --force       # mais paralelismo, re-baixa tudo
   ```

   O script é idempotente: arquivos já completos são ignorados (comparando o
   tamanho local com o remoto e o ETag). Downloads interrompidos deixam um
   arquivo `.part` e são retomados na próxima execução. Um resumo e um código
   de saída não-zero (quando algo falha) são fornecidos ao final.

   > **Nota sobre o WebDAV da Receita:** o compartilhamento fica atrás de um
   > balanceador cujos nós, às vezes, respondem ao `PROPFIND` com um `HTTP 200`
   > vazio (ou resetam a conexão) em vez do `207 Multistatus` esperado. Por
   > isso a listagem de pastas é repetida com espera até obter uma resposta
   > válida (o que costuma levar ~20 s). Sem isso, o script encontrava "nenhuma
   > pasta mensal" e não baixava nada.

2. **Descompactar os arquivos:**
   ```bash
   python 02_unzip-files.py
   ```
   Este script irá descompactar todos os arquivos `.zip` do diretório de arquivos compactados para o diretório de arquivos descompactados.

3. **Carregar os dados no SQLite:**
   ```bash
   python 03_load-to-sqlite.py
   ```
   Este script irá carregar os dados descompactados em um banco de dados SQLite. O arquivo do banco de dados será criado no diretório especificado em `processing_dir`. Só são carregadas as tabelas correspondentes aos arquivos presentes em `[arquivos].files_to_download`.

   Por padrão a carga usa **DuckDB** (`[sqlite].backend = "duckdb"`), que lê todos os arquivos de cada tipo de uma vez (em paralelo) e escreve direto no SQLite — bem mais rápido que a rota antiga com pandas. O backend legado continua disponível com `backend = "sqlite"`. Na primeira execução o DuckDB baixa a extensão `sqlite` (requer internet uma vez).

   Os arquivos da Receita são `latin1`. Ocasionalmente alguns arquivos grandes trazem bytes de controle da faixa `0x80–0x9F`, que o leitor latin-1 do DuckDB recusa (`File is not latin-1 encoded`). Quando isso acontece, o script cria uma cópia temporária do arquivo trocando esses bytes por espaço, recarrega e apaga a cópia — os arquivos originais não são alterados.

## Banco de Dados

O banco de dados SQLite contém as seguintes tabelas:

- `estabelecimento`: Informações detalhadas sobre cada estabelecimento (local da empresa).
- `empresas`: Informações gerais sobre cada empresa.
- `socios`: Informações sobre os sócios de cada empresa.
- `cnae`: Códigos e descrições de atividades econômicas.
- `motivo`: Códigos e descrições de motivos de situação cadastral.
- `municipio`: Códigos e descrições de municípios.
- `natureza`: Códigos e descrições de naturezas jurídicas.
- `pais`: Códigos e descrições de países.
- `qualificacao`: Códigos e descrições de qualificações de sócios.

Índices são criados nas colunas `cnpj` e `cnpj_basico` para acelerar as consultas.


## Testes

Os testes unitários (descoberta de arquivos, retomada, validação de
integridade e metadados) não dependem de rede:

```bash
python -m unittest discover -s tests -t . -v
```

## Licença

Este projeto está licenciado sob a Licença MIT. Consulte o arquivo [LICENSE](LICENSE) para obter detalhes.