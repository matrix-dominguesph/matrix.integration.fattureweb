# Testes

Sem framework (o repo não tem um): cada módulo é um script que imprime `[OK ]`/`[FALHA]` por
verificação e sai com código **1** se alguma falhar — serve para rodar na mão e para CI.

```bash
python -m testes.test_unitario     # offline: sem rede, sem .env, sem GCP
python -m testes.test_integracao   # bate na API real do Fattureweb: precisa do .env
```

**Nenhum dos dois escreve no BigQuery.** O estado da tabela gravada é simulado recortando o
próprio snapshot, então a suíte roda sem credencial de GCP e sem efeito colateral.

## `test_unitario.py` — offline

Usa o dublê `falso_fattureweb.FattureWebFalso`, que imita o comportamento real da API,
inclusive as esquisitices que motivaram o código de produção (todas verificadas contra a API
de verdade):

- `count=true` responde sempre 200, com `total: 0` quando nada casa;
- página de conjunto vazio responde **404**, não 200 com lista vazia;
- `data_atualizacao_inicio` filtra `data_atualizacao` e é **inclusivo**;
- parâmetro desconhecido é **ignorado em silêncio** (`ignora_filtro=True`).

E permite sabotar páginas (`falhas_por_skip={skip: n}`) para exercitar a repescagem e o
aborto sem depender de a rede cair de verdade.

Cobre: modelagem das colunas e tolerância a `conteudo=None`; o guarda-corpo
`_conferir_corte`; paginação (limit pequeno, limit grande, total múltiplo exato do limit);
conjunto vazio; corte incremental incluindo o caso da **fatura reprocessada**; repescagem
até 5 tentativas e aborto quando não recupera; `combinar` (upsert por `id_fatura`); e o ciclo
snapshot → corte → upsert com idempotência.

## `test_integracao.py` — API real

Precisa de `FATTUREWEB_*` no `.env` (aborta com código 2 se faltar). Confirma contra a API o
que o código assume:

- `data_atualizacao >= data_criacao` em **todas** as linhas — é a premissa que faz o corte
  por atualização pegar fatura nova *e* reprocessada numa passada só;
- offset de fuso **único** nas duas colunas de data, e `MAX` lexicográfico == `MAX`
  cronológico — é o que autoriza guardar as datas como STRING e usar o `MAX` direto como
  filtro;
- o conjunto filtrado por `data_atualizacao_inicio` é **superconjunto** do filtrado por
  `data_criacao`;
- o corte é inclusivo, o upsert não perde nem inventa linha, e a 2ª execução seguida não
  altera nada;
- as faturas coletadas por `cliente_id` são **todas** de instalações da carteira.

Uma verificação pode falhar por motivo legítimo: *"nenhuma linha inventada"* quebra se uma
fatura entrar no Fattureweb no meio da execução do teste (a base é viva). A mensagem diz
isso.
