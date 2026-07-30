# PRD — Monitor de Aquisição de Fatura (Fattureweb)

> Projeto: Melhoria GD (Épico) · Autor: Torres Agent · Atualizado: 2026-07-30 · Status: ativo
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
- **Histórico diário** do nº de UCs por estado/categoria (tendência de problemas).

## Escopo
**Entra:** coletar, **nível fatura**, todas as faturas de Matrix Fácil B com a **origem da captura**; classificar o **estado/categoria** de aquisição; materializar tabelas refinadas (histórico diário + tabela do dashboard); expor num **dashboard** (por cliente / distribuidora / estado / origem / data).
**Fica de fora (por ora):** correção automática de credenciais; validação do login no cadastro; mudanças na plataforma Fattureweb; notificação (fase posterior); outras modalidades de cliente.

## Usuários
**Operação GD** — cadastra credenciais, recebe/consulta e corrige; coordenação de Operações GD (visão agregada e tendência).

## Arquitetura (resumo — detalhe no ADR 0001)
Três camadas, um repo cada, com o BigQuery como contrato:
- **Ingestão (`…job`)** — Cloud Run Job: Fattureweb → tabela crua **nível fatura** (com origem da captura).
- **Regras de negócio (`…backend`)** — batch: cru → tabelas refinadas (histórico diário, tabela do dashboard).
- **Apresentação (`…frontend`)** — Next.js lendo o BQ direto → dashboard.

## Requisitos principais
1. **Coletar nível fatura** todas as faturas de Matrix Fácil B, com a **origem da captura** (site distribuidora · webcrawler · API distribuidora · e-mail).
2. **Classificar** estado (sucesso · aguardando · erro) e **categoria** do status (mapeadas do `descricao_status_webcrawler`; ver histórico diário).
3. **Materializar refinadas**: histórico diário (`tb_hist_status_webcrawler`, contagens por dia/estado/categoria) e a tabela que alimenta o dashboard.
4. **Expor no dashboard**: tabela por UC/fatura + KPIs + filtros; base para análise de cobertura e origem.
5. **Estender** a outras carteiras por configuração, sem tocar código.

## Riscos & questões em aberto
- **Schema nível-fatura** e o **enum de origem** ainda a mapear (input do Pedro) → vira ADR/spec.
- Fonte de cada origem (como distinguir site vs crawler vs API vs e-mail de forma confiável).
- Como o "recebido por e-mail" é registrado/rastreado.
- Cadência dos dois jobs batch (frescura do dashboard) e idempotência do histórico.
- Volume de faturas e falsos positivos.

---
*PRD 1-pager (Torres). Fonte da verdade: este `.md` canônico no `…job`; a página do Notion espelha (sincronizar).*
