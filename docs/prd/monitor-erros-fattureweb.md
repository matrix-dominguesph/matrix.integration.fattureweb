# PRD — Monitor de erros de aquisição de fatura (Fattureweb)

> Projeto: Melhoria GD (Épico) · Autor: Torres Agent · Data: 2026-07-24 · Status: ativo
> Notion: https://app.notion.com/p/3a7a953252ff81fe9503c75241e81ca4 · GitHub: docs/prd/monitor-erros-fattureweb.md
> Card no board: https://app.notion.com/p/3a7a953252ff81059b0ff5ae7a3bae7c

## Problema
Na modalidade **Energia Fácil B**, o cliente pede troca de titularidade para a Associação/Cooperativa **Terenas**; concluída a troca, entra numa carteira no **Fattureweb**, onde a Operação GD cadastra **login e senha** para o Fattureweb puxar a fatura do portal da distribuidora. Esse login/senha **não é testado no cadastro** — quando está errado, a aquisição da fatura falha. O erro só aparece **dentro da plataforma do Fattureweb**, exigindo checagem manual, o que **atrasa a correção**.

## Objetivo & métricas de sucesso
Dar **visibilidade proativa** aos erros de aquisição, encurtando o tempo entre a falha e a correção.
- Erros de aquisição visíveis **fora** da plataforma do Fattureweb.
- Redução do **tempo médio entre erro e correção** (MTTR).
- % de erros **detectados proativamente** (vs. descoberta manual) — meta próxima de 100%.

## Escopo
**Entra:** coletar os erros de aquisição do Fattureweb; expô-los num **dashboard** (por cliente / carteira / distribuidora / data) e/ou **notificar** o responsável pelo cadastro (Teams / e-mail); identificar quem cadastrou; escopo inicial = carteira **Energia Fácil B** (Terenas).
**Fica de fora:** correção automática de credenciais; validação do login no ato do cadastro (melhoria preventiva correlata — ver questões em aberto); mudanças na plataforma Fattureweb; outras modalidades de cliente (fase posterior).

## Usuários
**Operação GD** — o funcionário que cadastra login/senha (recebe a notificação / consulta o dash e corrige); coordenação de Operações GD (visão agregada dos erros).

## Requisitos principais
1. **Coletar os erros** de aquisição do Fattureweb (fonte a definir: API, banco, export ou leitura da carteira — já existe um `fattureweb/client.py` no repo do motor a reaproveitar).
2. **Expor fora da plataforma:** dashboard consultável e/ou notificação ao responsável, com cliente, carteira, distribuidora, data e o tipo de erro.
3. **Identificar o responsável** pelo cadastro para acionamento direto.
4. **Escopo inicial** na carteira Energia Fácil B; desenho que permita estender a outras carteiras.

## Riscos & questões em aberto
- **Fonte dos erros** no Fattureweb (API vs banco vs scraping) e sua estabilidade.
- O Fattureweb registra **quem cadastrou** o login? Se não, como mapear o responsável.
- **Canal** de notificação (Teams? e-mail?) e **granularidade** (por erro / resumo diário).
- **Dash novo** ou integrar a algo existente (ex.: Cockpit GD / boletins).
- Vale adicionar **validação do login no cadastro** (prevenção) além da visibilidade (detecção)?
- Volume de erros e falsos positivos.

---
*PRD 1-pager mantido pelo Torres Agent. Fonte da verdade: este `.md`; a página do Notion espelha.*
