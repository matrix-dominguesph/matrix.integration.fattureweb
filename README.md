# matrix.integration.fattureweb

> **Cópia pessoal — não é o canônico.** O repositório canônico deste pipeline é
> **`matrix.fattureweb.webcrawler.job`** (camada de ingestão das 3 camadas do projeto).
> Aqui é uma cópia mantida em paridade: toda alteração feita lá é refletida aqui, com
> `src/` e `testes/` idênticos arquivo por arquivo. Para abrir PR, revisar ou fazer deploy,
> use o canônico — este repo não tem workflow de deploy, de propósito.

**Camada de ingestão (raw)** do Monitor de Aquisição de Fatura (Fattureweb) — Cloud Run
Job que coleta do Fattureweb e grava as tabelas cruas no BigQuery. O PRD e os ADRs
canônicos vivem no `…job` e estão copiados em [`docs/`](docs/) para consulta.

## Como o escopo chegou aqui

A ideia original era pequena: **uma visualização das falhas do webcrawler do Fattureweb**.
Na modalidade Matrix Fácil B, a Operação GD cadastra login e senha da distribuidora para o
Fattureweb puxar a fatura, o cadastro não é testado, e quando a credencial está errada o
erro só aparece dentro da plataforma — logo, a correção atrasa. O primeiro recorte era
justamente dar visibilidade a esses erros fora do Fattureweb.

O problema **escalou**: mais do que saber quais crawlers falharam, o que importa é a
**visão de todos os clientes do produto Matrix Fácil B** — se a fatura de cada UC foi
obtida e, principalmente, **como** foi obtida, já que a captura acontece por quatro
caminhos diferentes (site da distribuidora, webcrawler, API da distribuidora e
encaminhamento de e-mail). O produto deixou de ser "monitor de erro do crawler" e passou a
ser **monitor de aquisição de fatura, no grão da fatura**, com cobertura e origem por
período. Daí as duas tabelas desta camada e a divisão do projeto em três camadas.

Histórico das duas leituras: o PRD original `monitor-erros-fattureweb` foi sucedido pelo
canônico [`docs/prd/monitor-fattureweb.md`](docs/prd/monitor-fattureweb.md).

## As três camadas

| Repositório | Camada | Papel |
| --- | --- | --- |
| **`…webcrawler.job`** (canônico) | Ingestão (raw) | Coleta do Fattureweb → tabelas cruas no BQ |
| `…webcrawler.backend` | Regras de negócio (refined) | Cru → tabelas refinadas que alimentam o dashboard |
| `…webcrawler.frontend` | Apresentação | Next.js lendo as refinadas direto do BQ |

O **BigQuery é o contrato** entre as camadas: o schema das tabelas é a interface, não há
chamada direta entre serviços. Decisão completa em
[`docs/adr/0001-arquitetura-3-camadas.md`](docs/adr/0001-arquitetura-3-camadas.md).

## O que este job produz

Duas tabelas, mesmo projeto e dataset, com **regimes de carga diferentes**:

| Tabela | Grão | Regime | Conteúdo |
| --- | --- | --- | --- |
| `BQ_TABLE` (`tb_status_webcrawler`) | instalação | **sempre** create-or-replace | status do último crawler por UC + `data_fim` da última tentativa |
| `BQ_TABLE_FATURAS` (`tb_faturas_matrix_facil_b`) | **fatura** | acumulativa, com **update incremental + upsert** | histórico de faturas por UC, com a **origem da captura** |

A primeira é um retrato do agora — não há histórico a preservar, então substituir é o certo.
A segunda é acumulativa: é ela que sustenta a análise de cobertura e origem ao longo do
tempo, e é o update incremental dela que justifica o job rodar agendado.

Colunas da tabela nível fatura: `id_fatura` (chave do grão), `cliente_instalacao`,
`distribuidora_sigla`, `id_instalacao`, `origem`, `data_emissao`, `data_criacao` e
`data_atualizacao`. `origem` é **passthrough** do `conteudo.fatura_origem` — hoje vem `SITE`,
`API`, `WEBCRAWLER` e `EMAIL`, e um valor novo entra sem quebrar nada (não há enum no
código).

### Fluxo (7 etapas, `src/main.py`)

1. **auth** — `TokenSession.login()`
2. **coleta** — `listar_instalacoes`: carteira → clientes → instalações (paginação paralela)
3. **modelagem** — `montar_tabela`: 6 colunas do registro de `/instalacoes`
4. **enriquecimento** — maior `data_fim` de execução do crawler, só para status ≠ sucesso
5. **carga** — create-or-replace de `BQ_TABLE`
6. **faturas** — lê a tabela gravada, coleta `/faturas?cliente_id=…` paginado e faz o upsert
7. **carga das faturas** — create-or-replace da tabela combinada

A etapa 6 usa o filtro `cliente_id` (mesmo escopo da etapa 2) em vez de iterar as
instalações. Não é só custo — é o que **desacopla** a tabela de faturas da listagem de
instalações. Ver o porquê, com os números, no topo de
[`src/pipeline/faturas.py`](src/pipeline/faturas.py).

### Snapshot x update incremental

A etapa 6 decide sozinha o modo, a partir do que já está gravado (`src/bq/reader.py`):

- **tabela não existe ou está vazia** → snapshot completo (sem corte);
- **tabela com dados** → `data_atualizacao_inicio = MAX(data_criacao)` da tabela.

O corte vai sobre **`data_atualizacao`**, não sobre `data_criacao`, e isso é o ponto. A doc do
Fattureweb avisa que *"podem ocorrer reprocessamentos de faturas"* e que o consumidor deve
identificar uma fatura já importada **e atualizá-la**. Medido na carteira: **139 de 1973
linhas (7%)** têm `data_atualizacao > data_criacao` — o reprocessamento atualiza a mesma
linha, preservando a `data_criacao` original. Um corte por `data_criacao` nunca traria essas
de volta.

Filtrar por `data_atualizacao_inicio` resolve **os dois casos numa passada só**, porque
`data_atualizacao >= data_criacao` sempre (conferido: 0 exceções em 1973 linhas, e o teste de
integração checa isso a cada execução). Logo o conjunto por atualização é superconjunto do
conjunto por criação — com corte em 2026-06-01: 1021 por criação ⊆ 1061 por atualização,
zero fora, e as 40 extras são exatamente as reprocessadas. Fatura nova tem
`data_atualizacao == data_criacao`, então também entra.

O corte usado é o `MAX(data_criacao)`, não o `MAX(data_atualizacao)`: os dois funcionam, mas
o primeiro é `<=` o segundo, o que dá uma janela de sobreposição de graça — margem contra uma
fatura ser atualizada no meio da varredura anterior.

Como o lote pode conter faturas **já gravadas em versão antiga**, a carga é um **upsert** por
`id_fatura` (`pipeline/faturas.py::combinar`): a versão que veio da API vence, e a tabela é
reescrita create-or-replace. Não é `append` — append manteria a versão velha para sempre. A
combinação é feita em pandas em vez de `MERGE` no BigQuery porque nesta escala (~2 mil linhas,
poucas novas por dia) é mais simples e a lógica fica testável sem BigQuery; se a tabela chegar
à ordem de centenas de milhares de linhas, trocar por staging + `MERGE ON id_fatura`. O ciclo
é idempotente: rodar duas vezes seguidas não altera a tabela.

Para reprocessar a tabela de faturas do zero: `FATURAS_FULL_REFRESH=true`.

**Carga parcial é proibida por construção.** Se alguma página de `/faturas` não voltar (falha
de transporte — conexão derrubada, rara e intermitente), a coleta repete **a mesma
requisição** (mesmos `cliente_id`, `limit` e `skip`) **em série, até 5 tentativas, com espera
crescente**; se ainda assim faltar alguma, o job levanta `ColetaFaturasIncompleta` e **não
grava nada**. Um 404 não conta como falha e não é repescado — é como o endpoint diz "conjunto
vazio", o que pode acontecer legitimamente se o resultado encurtar no meio da varredura. O motivo: gravar uma tabela com buraco perde
essas faturas para sempre — o corte da execução seguinte já passou por elas. Falhar alto e
refazer a janela na próxima execução é mais seguro. A tabela de status não tem esse risco (é
create-or-replace a partir de outra fonte, se autocorrige) e já foi carregada na etapa 5.

### Transporte: conexão reusada e retry para todas as etapas

Antes dessa proteção de pipeline há uma no transporte. Todas as requisições passam por uma
única `requests.Session` (`src/fattureweb/session.py`), com `HTTPAdapter` configurado — em
vez de `requests.request(...)`, que abria uma conexão, fazia o handshake TLS, usava uma vez e
descartava (numa varredura de 54 páginas eram 54 handshakes atravessando a inspeção TLS da
rede corporativa). Dois efeitos:

- **reuso de conexão** — a varredura de 54 páginas caiu de ~3,6s para **2,08s**;
- **retry no nível da conexão** — o `urllib3` repete a mesma requisição sozinho (até 5
  tentativas, espera crescente), antes de a exceção chegar ao código de negócio. Vale para
  **todas** as etapas: instalações, webcrawler, faturas e o próprio login.

Não é repetido, de propósito: **401** (quem trata é o `TokenSession.request`, refazendo o
login), **404** (aqui significa "conjunto vazio", não falha) e 4xx em geral (pedido
malformado não melhora com insistência).

Isso reduz a frequência da falha nas duas etapas, mas não substitui o
`ColetaFaturasIncompleta` — o transporte não tem como decidir "não gravar tabela com buraco".
Os dois convivem: retry no transporte, decisão de não gravar parcial no pipeline.

## Testes

```bash
python -m testes.test_unitario     # offline: sem rede, sem .env, sem GCP
python -m testes.test_integracao   # bate na API real: precisa do .env
```

Nenhum dos dois escreve no BigQuery. Detalhes em [`testes/README.md`](testes/README.md).

## Configuração

Tudo por variável de ambiente (Pydantic Settings, `src/config.py`). Copie `env.example`
para `.env` e preencha — **nunca comite o `.env`**.

| Variável | Default | Descrição |
| --- | --- | --- |
| `FATTUREWEB_BASE_URL` / `_USERNAME` / `_PASSWORD` | — | credenciais da API do Fattureweb |
| `GCP_PROJECT` / `BQ_DATASET` | — | destino no BigQuery (auth por ADC) |
| `BQ_TABLE` | — | tabela de status por instalação |
| `BQ_TABLE_FATURAS` | `tb_faturas_matrix_facil_b` | tabela nível fatura |
| `PAGE_SIZE` / `MAX_WORKERS` | 180 / 8 | `limit` da paginação e threads paralelas |
| `STATUS_SUCESSO_ID` | 10 | `status_webcrawler_id` de sucesso (não é enriquecido) |
| `WEBCRAWLER_CHUNK_SIZE` | 20 | ids por chamada no enriquecimento do crawler (teto da querystring) |
| `FATURAS_FULL_REFRESH` | `false` | força snapshot completo da tabela de faturas |
| `CLIENTE_IDS` | `[208062, 208063]` | clientes da carteira 2768 (MATRIX FÁCIL B), em JSON |

Nunca use comentário na mesma linha de um valor no `.env`: o `python-dotenv` remove
comentário inline, mas o loader de `.env` do debugger do VS Code **não** — e a variável de
ambiente ganha do arquivo, então o valor chega com o comentário grudado e quebra a
validação dos campos numéricos.

Estender a outras carteiras é trocar `CLIENTE_IDS` por env, sem tocar código.

## Rodando

```bash
pip install -r requirements.txt
cp env.example .env   # preencher
python -m src.main
```

Auth do BigQuery por **ADC**: local via `gcloud auth application-default login`; no Cloud
Run, a service account do próprio Job. O `truststore` no bootstrap do `main.py` usa o trust
store do SO — necessário na rede corporativa (inspeção TLS), inócuo no Cloud Run.

## Deploy

**Não há deploy a partir deste repo, de propósito** — o workflow foi removido. O deploy do
Cloud Run Job vive só no canônico `matrix.fattureweb.webcrawler.job`, que reusa o workflow do
org (`Matrix-Energia/matrix.data.pipelines/.github/workflows/gcp-cloud-run-job-build.yaml`),
região `us-east1`, em push para `main`, `hml` e `dev`.

## Documentação

- PRD canônico: [`docs/prd/monitor-fattureweb.md`](docs/prd/monitor-fattureweb.md)
- ADRs: [`docs/adr/`](docs/adr/) — 0001 (arquitetura 3 camadas), 0002 (layout do Cloud Run Job)
- Diagrama navegável: `docs/arquitetura.html`

O comportamento real da API do Fattureweb (filtros, projeção obrigatória, 404 em conjunto
vazio, semântica de `skip`) está documentado no topo de
[`src/pipeline/faturas.py`](src/pipeline/faturas.py), cada afirmação verificada contra a
API e contra a doc oficial da coleção "Fattureweb API Pública".
