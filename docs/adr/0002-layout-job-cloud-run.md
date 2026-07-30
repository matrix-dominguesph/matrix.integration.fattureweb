# ADR 0002 — Layout do repositório de ingestão (Cloud Run Job)

- Status: aceito
- Data: 2026-07-24
- Autor: Arquimedes
- Nota (2026-07-30): incorporado ao conjunto canônico do repo-âncora `…job` como
  **ADR 0002** (era ADR 0001 do repo de backend, antes da separação em 3 camadas —
  ver [[0001-arquitetura-3-camadas]]). Descreve o layout `src/` da **camada de
  ingestão** (`…webcrawler.job`). Onde o texto abaixo cita `.env.example`, leia
  `env.example` (renomeado por regra de push protection do org). Conteúdo original
  preservado.

## Contexto

O repo `matrix.integration.fattureweb` nasceu como sandbox: `config.py` (Pydantic
Settings) e `listar_clientes_carteira.py` (script autocontido com `TokenSession`,
listagem paginada e `montar_tabela`) na raiz, mais um `execucao.ipynb`. O objetivo
(PRD `monitor-erros-fattureweb`) é dar visibilidade proativa aos erros de aquisição
de fatura: coletar o status do webcrawler por instalação e materializar num BigQuery
consumível por dashboard/notificação.

Isso precisa virar um pipeline batch fim-a-fim rodando como **Cloud Run Job**, sob
restrições de deploy dadas e imutáveis:

- Entrypoint `CMD ["python3", "-m", "src.main"]` → exige pacote `src/` com `main.py`.
- Dockerfile: `COPY . .`, `PYTHONPATH=/app`, `pip install -r requirements.txt`,
  `TZ=America/Sao_Paulo`.
- CI: `.github/workflows/job-build.yaml` (reusable workflow Cloud Run Job) em push
  nas branches main/hml/dev.
- Hoje o import é `from config import settings`, que só resolve com o CWD na raiz —
  incompatível com `python -m src.main`.

O pipeline a orquestrar: (1) auth via `TokenSession`; (2) listar instalações da
carteira 2768 paginadas (180) com `ThreadPoolExecutor`; (3) montar DataFrame;
(4) enriquecer com a maior `data_fim` do webcrawler para `status_webcrawler_id != 10`
(também com threads); (5) carregar em `matrix-data-analytics-prd.ds_geracao_distribuida.tb_status_webcrawler`
como create-or-replace.

## Decisão

Vamos organizar o repositório com todo o código de produção sob um pacote `src/`,
segmentado por responsabilidade, mantendo config/infra na raiz e movendo o notebook
para `sandbox/`. O `config.py` da raiz **vira `src/config.py`**.

Árvore-alvo:

```
matrix.integration.fattureweb/
├── Dockerfile
├── requirements.txt
├── .env                       # local, gitignored
├── .env.example               # template versionado
├── .gitignore
├── README.md
├── .github/workflows/job-build.yaml
├── docs/{prd/, adr/}
├── sandbox/execucao.ipynb      # exploração, fora do pacote de produção
└── src/
    ├── __init__.py
    ├── main.py                 # orquestra o pipeline fim-a-fim
    ├── config.py               # Pydantic Settings: env + destino BQ + tuning
    ├── fattureweb/
    │   ├── __init__.py
    │   └── session.py          # TokenSession: login + request c/ re-login em 401
    ├── pipeline/
    │   ├── __init__.py
    │   ├── instalacoes.py      # listar_instalacoes paginado + threads; _extrair_total
    │   ├── webcrawler.py       # enriquecimento: maior data_fim por instalação
    │   └── tabela.py           # montar_tabela → DataFrame (schema de saída)
    └── bq/
        ├── __init__.py
        └── loader.py           # create-or-replace no BigQuery
```

**Contrato de import:** com `PYTHONPATH=/app` + `python -m src.main`, `src` é um
pacote. Import canônico **absoluto** ancorado no pacote: `from src.config import
settings`, `from src.fattureweb.session import TokenSession`, etc. Todo diretório sob
`src/` tem `__init__.py`. O antigo `from config import settings` some.

**Camada de acesso vs. domínio:** `fattureweb/session.py` é transporte puro
(autenticação + `request` genérico com re-login em 401) e **não conhece endpoints de
negócio**. O conhecimento de endpoint (`/instalacoes`, `/webcrawlers/execucoes`) vive
nos módulos de `pipeline/`, que recebem uma `TokenSession` por injeção. Isso mantém
`session.py` reusável (inclusive alinhável ao `client.py` do `matrix.gd.motor.calculo`).

**Settings (`src/config.py`)** ganha, além das 3 credenciais Fattureweb existentes:

- `GCP_PROJECT: str = "matrix-data-analytics-prd"` (destino BQ; override por env).
- `BQ_DATASET: str = "ds_geracao_distribuida"` e `BQ_TABLE: str = "tb_status_webcrawler"`.
- Tuning com default: `PAGE_SIZE: int = 180`, `MAX_WORKERS: int = 8`,
  `STATUS_SUCESSO_ID: int = 10`.
- Escopo de carteira: `CLIENTE_IDS: list[int] = [208062, 208063]` (carteira 2768),
  para estender a outras carteiras por env sem tocar código.

**Auth GCP:** ADC do próprio Cloud Run Job (service account do job) —
`bigquery.Client(project=settings.GCP_PROJECT)` resolve credencial sem key file.
Nenhum JSON de service account versionado nem em env.

## Consequências

**Positivas:**
- Import determinístico e greppável; roda idêntico local (`python -m src.main`) e no
  container, sem hacks de `os.chdir`/CWD.
- Coesão por responsabilidade (auth · coleta · enriquecimento · modelagem · carga):
  cada etapa testável e substituível isoladamente; fronteiras explícitas.
- `session.py` desacoplado de endpoint fica reusável e convergente com o motor GD.
- Extensão a outras carteiras vira mudança de env, não de código (atende o PRD).

**Negativas / custos:**
- Refactor inicial: quebrar o script monolítico em módulos e reescrever todos os
  imports para `from src....`; adicionar `__init__.py` em cada pasta.
- Mais arquivos para um pipeline pequeno (custo de navegação vs. um único script).
- Diverge do padrão legado de repos-irmãos que usam `from utils.x` + `os.chdir` +
  `os.getenv`; ganho de consistência interna ao custo de não copiar o vizinho.

**Neutras:**
- `truststore.inject_into_ssl()` (TLS corporativo) segue como best-effort no boot;
  inócuo no Cloud Run, necessário para rodar atrás do proxy corporativo.
- Tabela é snapshot full a cada execução; não há partição/histórico por ora.

## Alternativas consideradas

- **Manter script único na raiz + entrypoint chamando-o** — rejeitada: viola a
  restrição `python -m src.main` e o `from config import settings` dependente de CWD;
  não escala para as 5 etapas com testabilidade.
- **Espelhar o padrão legado (`src/{handler,service,utils}` + `os.chdir` + `os.getenv`
  + `dotenv`)** — rejeitada: o `os.chdir` no topo do `main` é um hack para viabilizar
  imports rasos; com `python -m src.main` + `PYTHONPATH=/app` o import absoluto
  `from src....` é limpo e não precisa dele. Pydantic Settings > `os.getenv` espalhado
  (padrão da skill `engenharia-dados-matrix`).
- **`config.py` permanecer na raiz** — rejeitada: com o pacote `src` como raiz de
  import, `from src.config import settings` é coerente; um `config.py` na raiz exigiria
  `/app` como caminho de import concorrente e reintroduziria a dependência de CWD.
- **Idempotência via append + dedupe** — rejeitada para o caso de uso (monitor de
  estado atual): create-or-replace (`WRITE_TRUNCATE` / `if_exists="replace"`) já é
  idempotente e mais simples. Histórico, se pedido, vira novo ADR (partição por data
  de ingestão).
```
