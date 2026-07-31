# PRD — Monitor de Aquisição de Fatura (Fattureweb)

> Projeto: Melhoria GD (Épico) · Autor: Torres Agent · Atualizado: 2026-07-31 · Status: ativo
> Doc canônico (3 camadas). GitHub: `matrix.fattureweb.webcrawler.job/docs/prd/monitor-fattureweb.md`
> Notion: 📁 Monitor de Aquisição de Fatura (Fattureweb) — https://app.notion.com/p/3a7a953252ff81fe9503c75241e81ca4
> Arquitetura: ver `docs/adr/0001-arquitetura-3-camadas.md`
> Sucede o PRD `monitor-erros-fattureweb` (escopo ampliado: de "erros" para "aquisição nível fatura").

## Problema
Na modalidade **Matrix Fácil B** (Energia Fácil B), o cliente troca a titularidade para a Associação/Cooperativa **Terenas** e entra numa carteira no **Fattureweb**, onde a Operação GD cadastra credenciais para puxar a fatura da distribuidora. Hoje falta **visibilidade** de duas coisas ao mesmo tempo: (1) **se** a fatura de cada UC foi obtida e, quando não, **por quê** (credencial errada, site indisponível, etc.); e (2) **como** cada fatura foi obtida (site da distribuidora, webcrawler, API da distribuidora ou encaminhamento de e-mail). Isso só é visível dentro da plataforma, atrasando correção e impedindo análise de cobertura/origem.

## Objetivo & métricas de sucesso
Dar **visibilidade proativa da aquisição de fatura**, **nível fatura**, para a Operação GD.
- Erros de aquisição visíveis **fora** do Fattureweb (↓ MTTR entre falha e correção).
- % de erros **detectados proativamente** (meta ~100%).
- **Cobertura**: % de faturas obtidas por período, e **distribuição por origem** da captura (site / crawler / API / e-mail).
- **Tendência da origem**: distribuição mês a mês dos caminhos de captura (12 meses), que é o que mostra migração de canal. *Não* há tendência do estado do crawler — ver escopo.
- **Clientes atrasados** em relação ao próprio ciclo de emissão (não ao mês-calendário).

## Escopo
**Entra:** coletar, **nível fatura**, todas as faturas de Matrix Fácil B com a **origem da captura**; classificar o **estado/categoria** de aquisição; materializar tabelas refinadas; expor num **dashboard** (por cliente / distribuidora / estado / origem / data).
**Fica de fora (por ora):** correção automática de credenciais; validação do login no cadastro; mudanças na plataforma Fattureweb; notificação (fase posterior); outras modalidades de cliente; **histórico diário do estado do crawler** (a `tb_hist_status_webcrawler` que versões anteriores deste PRD previam foi retirada do escopo em 2026-07-31 — decisão, não esquecimento). Consequência a conhecer: não há como responder "o nº de UCs em erro está subindo ou caindo?", porque as tabelas refinadas são todas retrato do agora. Há série temporal de **faturas emitidas** (`tb_refined_origem_mensal`, 12 meses), não do **estado do crawler**. Retomar exige uma tabela em modo append, a única exceção ao padrão create-or-replace da camada.

## Usuários
**Operação GD** — cadastra credenciais, recebe/consulta e corrige; coordenação de Operações GD (visão agregada e tendência de origem).

## Arquitetura (resumo — detalhe no ADR 0001)
Três camadas, um repo cada, com o BigQuery como contrato:
- **Ingestão (`…job`)** — Cloud Run Job: Fattureweb → tabela crua **nível fatura** (com origem da captura).
- **Regras de negócio (`…backend`)** — batch: cru → 6 tabelas refinadas que o dashboard consome.
- **Apresentação (`…frontend`)** — Next.js lendo o BQ direto → dashboard.

## Requisitos principais
1. ✅ **Coletar nível fatura** todas as faturas de Matrix Fácil B, com a **origem da captura**. Feito: `tb_faturas_matrix_facil_b`, uma linha por fatura, com `origem` ∈ {`SITE`, `API`, `WEBCRAWLER`, `EMAIL`} — passthrough, sem enum fixo no código.
2. ✅ **Classificar** estado (sucesso · aguardando · erro). Feito na camada refinada, coluna `estado` de `tb_refined_uc_status`. A **categoria** do status (agrupar as ~9 descrições em famílias de causa) **não** foi feita — hoje a tela mostra a `descricao_status` crua.
3. ✅ **Materializar refinadas**: 6 tabelas — `tb_refined_cliente_por_uc`, `tb_refined_uc_status`, `tb_refined_cliente_faturas`, `tb_refined_fatura_historico`, `tb_refined_origem_mensal` e `tb_refined_kpis`. Todas `CREATE OR REPLACE` a cada execução.
4. ✅ **Expor no dashboard**: duas abas, cards, gráfico de origem por mês, tabela por cliente com drilldown e busca com autocomplete.
5. ✅ **Estender** a outras carteiras por configuração: `CLIENTE_IDS` por env, sem tocar código.

## Questões resolvidas
- **Schema nível-fatura e enum de origem** (era o principal risco): mapeados. O grão é a fatura, chave `id_fatura`, e a origem vem de `conteudo.fatura_origem`. As quatro origens aparecem na base: SITE, API, WEBCRAWLER, EMAIL. O código não enumera o campo, então um 5º valor entra sem quebrar.
- **Como distinguir as origens**: não precisou de heurística — a própria API informa em `conteudo.fatura_origem`.
- **Idempotência**: garantida. A camada refinada é `CREATE OR REPLACE` a cada execução; a tabela nível fatura usa upsert por `id_fatura` (a versão da API vence), então reprocessamento não duplica.
- **Nome do cliente**: `cliente_apelido` é o **produto** (`MATRIX FÁCIL B - COOPERATIVA` / `- ASSOCIAÇÃO`), não o cliente. O nome do cliente vem da fatura, mas depois da troca de titularidade a fatura sai no nome da Terenas — em 531 de 751 UCs o nome mais recente é o titular coletivo. A regra atual pega o nome mais recente que **não** seja a Terenas (577 nomes distintos, contra 212). É heurística: a fonte correta para 100% das UCs é o módulo TTs do **Zoho CRM**.

## Riscos & questões em aberto
- **Categoria do status** (requisito 2) não implementada: as ~9 descrições do `descricao_status_webcrawler` não estão agrupadas em famílias de causa (credencial · site indisponível · instalação não encontrada · download).
- **Cliente vindo do Zoho** em vez da heurística: cobriria as 146 UCs que hoje ficam com o nome do titular coletivo.
- **Fatura reprocessada não é reconciliada por conteúdo**: o update incremental pega quem mudou (via `data_atualizacao`), mas se a plataforma alterar uma fatura antiga sem mexer nessa data, não vemos.
- **Cadência dos jobs**: a ingestão e a camada refinada precisam rodar em sequência, e o agendamento da segunda ainda não existe.
- **Notificação** ao responsável pelo cadastro (fase posterior) segue fora de escopo.

---
*PRD 1-pager (Torres). Fonte da verdade: este `.md` canônico no `…job`; a página do Notion espelha (sincronizar).*
