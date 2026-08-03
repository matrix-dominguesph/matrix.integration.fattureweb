"""Coleta de faturas (grão = fatura) — tabela ``tb_faturas_matrix_facil_b``.

Consulta ``GET /faturas?cliente_id=<csv>``, paginado, e modela uma linha por fatura. O
escopo é o mesmo da etapa de instalações: os ``CLIENTE_IDS`` da carteira MATRIX FÁCIL B.
Estender a outras carteiras é mudar essa lista por env, sem tocar código.

Dois modos, mesmo caminho:
  - **snapshot completo** (``atualizadas_desde=None``): traz todo o histórico;
  - **update incremental** (``atualizadas_desde=<MAX(data_criacao) da tabela>``): traz o
    que entrou OU mudou desde o corte. Quem decide o modo é o ``main``, a partir do estado
    da tabela no BQ.

Por que o corte é sobre ``data_atualizacao`` e não sobre ``data_criacao``
------------------------------------------------------------------------
A doc do Fattureweb avisa que *"podem ocorrer reprocessamentos de faturas"* e que o
consumidor deve ser capaz de identificar uma fatura já importada **e atualizá-la**. Medido
na carteira: **139 de 1942 linhas (7,2%)** têm ``data_atualizacao > data_criacao`` —
defasagem mediana de 19 dias, máxima de 207. Ou seja, o reprocessamento **atualiza a mesma
linha** (mesmo ``id``, ``data_criacao`` original preservada); só em 13 casos apareceu uma
segunda fatura para o mesmo par (instalação, mês de referência). Um corte por
``data_criacao`` nunca traria essas 139 de volta.

Filtrar por ``data_atualizacao_inicio`` resolve **os dois casos numa passada só**, porque
``data_atualizacao >= data_criacao`` sempre (conferido: 0 exceções em 1962 linhas). Logo o
conjunto por atualização é superconjunto do conjunto por criação — medido com corte em
2026-06-01: 1021 por criação ⊆ 1061 por atualização, zero fora, e as 40 extras são
exatamente as reprocessadas. Fatura nova tem ``data_atualizacao == data_criacao``, então
também entra.

O corte usado é o ``MAX(data_criacao)`` da tabela, não o ``MAX(data_atualizacao)``. Os dois
funcionam, mas o primeiro é ``<=`` o segundo, o que dá uma janela de sobreposição de graça —
margem contra uma fatura ser atualizada no meio da varredura anterior. O custo é reprocessar
algumas linhas que já estavam corretas, e o upsert absorve isso sem duplicar.

Comportamento da API — cada item verificado contra a API real e contra a doc oficial
(``documenter.getpostman.com/view/12210526/UyrAFcnt``, coleção "Fattureweb API Pública"):

  - **a projeção DEFAULT não traz ``conteudo`` nem ``distribuidora_sigla``** (vinham
    ``None``): o header ``Fatture-SearchFields`` é obrigatório, e ``conteudo`` vem inteiro
    (não aceita caminho aninhado);
  - ``data_atualizacao_inicio`` = "faturas com **data de atualização no sistema maior ou
    igual** a esse campo" (doc) — **inclusivo**, confirmado na API. Aceita ``2026-07-30``,
    o ISO completo com microssegundos e o formato da doc (``2026-07-30 00:00:00 -03:00``).
    O par ``data_inicio``/``data_fim`` existe e filtra ``data_criacao`` ("data de inserção
    no sistema"); não é o que usamos, pelo motivo acima;
  - **parâmetro desconhecido é ignorado em silêncio** (``data_criacao_inicio``,
    ``dataInicio`` etc. devolveram o total inteiro, sem erro). Um typo no nome do filtro
    não falha — vira um snapshot completo disfarçado de update. Daí o guarda-corpo em
    ``_conferir_corte``;
  - ``count=true`` responde **sempre 200**, com ``total: 0`` quando nada casa. Já a página
    devolve ``404 {"status":"erro","mensagem":"Faturas não encontradas."}`` para conjunto
    vazio — e também quando ``skip`` passa do fim. Por isso a paginação é dirigida pelo
    ``total`` (``coletar_paginado``): com ``total=0`` nenhuma página é pedida, e nunca se
    pede página além do fim;
  - ``skip`` é **offset**, não número de página (a doc diz "número da página");
  - ``sort=<campo>`` só é aceito se o campo estiver na projeção (``sort=id`` devolve 400
    sem ``id`` no header). Não usamos ``sort``.

**Por que ``cliente_id`` e não blocos de ``instalacao_id``.** A primeira versão iterava as
instalações em blocos de 20 ids (o CSV na querystring tem teto). Trocar por ``cliente_id``,
que não tem esse teto, resolve três coisas:

1. **Desacopla da etapa de instalações — o motivo principal.** Pelos blocos, a completude
   da tabela de faturas dependia de ``/instalacoes`` ter devolvido *todas* as páginas.
   Observado uma vez: uma execução em que ``/instalacoes`` reportou ``total=854`` mas
   entregou 674 (uma página caiu por falha de transporte — rara, ver
   ``ColetaFaturasIncompleta``) coletou **1537 faturas em vez de 1940** — 403 a menos, sem
   erro nenhum visível na etapa de faturas.
2. **Custo.** Medido: snapshot 68 -> 12 requisições (3,04s -> 2,26s); update incremental
   34 -> 2 requisições (1,44s -> 0,42s). O peso não é o volume de dados (no update os
   lotes são minúsculos) e sim o round-trip: pelos blocos o job paga um ``count`` por bloco
   só para descobrir que não há nada novo, em quase todos eles.
3. **Menos código**: a paginação vira uma chamada ao ``coletar_paginado`` já usado pelas
   instalações, dirigida pelo ``total`` — o que também elimina o passo de paginação a mais
   que levava 404 quando o total era múltiplo exato de ``limit``.

Escopo, conferido: as instalações das faturas coletadas por ``cliente_id`` estão **todas**
dentro das listadas por ``/instalacoes`` para os mesmos clientes (747 de 854, zero fora), e
``carteira_id=2768`` devolve o mesmo total — ou seja, ``CLIENTE_IDS`` delimita a carteira
MATRIX FÁCIL B e não vaza outro produto.
"""

from __future__ import annotations

import time

import pandas as pd
import requests

from src.fattureweb.session import TokenSession
from src.pipeline.instalacoes import coletar_paginado

# Projeção pedida à API (header ``Fatture-SearchFields``). Fonte única: exatamente o que
# o ``montar_tabela_faturas`` lê.
CAMPOS_FATURA = [
    'id',
    'instalacao_id',
    'distribuidora_sigla',
    'data_criacao',
    'data_atualizacao',
    'conteudo',
]

# Colunas de saída -> rótulo de negócio (o dashboard rotula; o BQ não aceita espaço,
# barra nem acento em nome de coluna, daí o snake_case aqui).
#   id_fatura           -> chave do grão (``dados[i].id``); é a chave do upsert
#   cliente_instalacao  -> "Cliente / Instalação"
#   distribuidora_sigla -> "Distribuidora"
#   id_instalacao       -> "Instalação"   (mesmo nome da outra tabela, para join)
#   origem              -> "Origem"       (SITE · API · WEBCRAWLER · EMAIL — e o que vier:
#                                          é passthrough, não há enum no código)
#   data_emissao        -> "Data Emissão"
#   data_vencimento     -> "Vencimento"    (do documento, não do sistema: quando o cliente
#                                           precisa pagar. Vem em `conteudo.fatura`, ao lado
#                                           da emissão, e estava em 60/60 faturas na amostra
#                                           que conferi na API antes de adicionar.)
#   data_criacao        -> "Data Criação"      (inserção no sistema; base do corte)
#   data_atualizacao    -> "Data Atualização"  (é o campo que o filtro incremental usa;
#                                               > data_criacao = fatura reprocessada)
COLUNAS_SAIDA = [
    'id_fatura',
    'cliente_instalacao',
    'distribuidora_sigla',
    'id_instalacao',
    'origem',
    'data_emissao',
    'data_vencimento',
    'data_criacao',
    'data_atualizacao',
]

# Repescagem de página que falhou: tentativas e espera entre elas (segundos).
TENTATIVAS_REPESCA = 5
ESPERA_BASE_REPESCA = 0.5


class ColetaFaturasIncompleta(RuntimeError):
    """Alguma página de ``/faturas`` não voltou, mesmo depois das repescagens.

    Existe para **impedir a carga parcial**. A tabela é reescrita a partir da combinação do
    que já estava gravado com o que a API devolveu; gravar uma combinação com buraco
    perderia linhas de vez, porque o corte da execução seguinte já passou por elas. Melhor o
    job falhar alto e a execução seguinte refazer a janela — a tabela fica intacta, e a de
    status (que é sempre create-or-replace a partir de outra fonte) já foi carregada na
    etapa anterior.

    A falha é de **transporte**, não da API: ``SSLError``/conexão derrubada, a requisição
    não completa (a API nunca respondeu erro nesses casos). É **intermitente e rara** —
    3 incidentes ao longo de um dia de execuções, e zero numa medição controlada de 424
    requisições. Justamente por ser rara é que precisa de guarda-corpo: quando acontece, o
    efeito sem ele é silencioso e definitivo (uma página a menos, 37 de 1940 linhas, sem
    nada quebrar).
    """


def _conferir_corte(registros: list, corte: str, campo: str = 'data_atualizacao') -> list[str]:
    """Guarda-corpo contra o filtro ignorado em silêncio.

    Se o nome do filtro não fosse reconhecido, a API devolveria o histórico inteiro sem
    erro. Comparação lexicográfica é válida aqui porque as datas vêm em ISO-8601 de largura
    fixa e com o mesmo offset (``-03:00``); offset misturado é avisado à parte, porque aí o
    ``MAX`` lexicográfico usado como corte deixaria de valer.
    """
    avisos: list[str] = []
    anteriores = [r for r in registros if (r.get(campo) or '') < corte]
    if anteriores:
        avisos.append(
            f"corte {campo}>={corte!r} parece ter sido IGNORADO pela API: "
            f"{len(anteriores)} de {len(registros)} registro(s) têm {campo} anterior ao "
            f"corte (ex.: {anteriores[0].get(campo)!r})"
        )
    for coluna in ('data_criacao', 'data_atualizacao'):
        offsets = {(r.get(coluna) or '')[-6:] for r in registros if r.get(coluna)}
        if len(offsets) > 1:
            avisos.append(
                f"{coluna} com offsets diferentes {sorted(offsets)} — o MAX lexicográfico "
                f"usado como corte deixa de ser confiável"
            )
    return avisos


def _repescar(
    ts: TokenSession,
    url: str,
    params: dict,
    headers: dict,
    falhas: list,
    *,
    page_size: int,
    tentativas: int = TENTATIVAS_REPESCA,
) -> tuple[list, list, list[str]]:
    """Repete a MESMA requisição das páginas que falharam — mesmos ``cliente_id``, ``limit``
    e ``skip``. Retorna ``(registros, restantes, avisos)``.

    Em série e com espera crescente de propósito: a falha típica é a conexão cair sob carga,
    então insistir no mesmo instante e em paralelo tende a cair de novo. Por isso a repesca
    roda depois de a varredura paralela terminar.

    **404 não é falha** e não é repescado: é como o endpoint diz "conjunto vazio". Pode
    acontecer legitimamente se linhas forem removidas no meio da varredura, encurtando o
    resultado — a última página deixa de existir. Insistir 5 vezes nela e depois abortar o
    job seria errado.
    """
    registros: list[dict] = []
    avisos: list[str] = []
    pendentes = list(falhas)

    for tentativa in range(1, tentativas + 1):
        if not pendentes:
            break
        if tentativa > 1:
            time.sleep(ESPERA_BASE_REPESCA * (2 ** (tentativa - 2)))  # 0.5s, 1s, 2s, 4s
        print(
            f"Faturas: repescando {len(pendentes)} página(s) "
            f"(tentativa {tentativa}/{tentativas})"
        )
        ainda: list = []
        for page, skip, _motivo in pendentes:
            try:
                data = ts.request(
                    'GET', url,
                    params={**params, 'limit': page_size, 'skip': skip},
                    headers=dict(headers),
                ).json()
                if data.get('status') != 'sucesso':
                    ainda.append((page, skip, f"status={data.get('status')!r}"))
                    continue
                registros.extend(data.get('dados') or [])
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    print(f"    página {page} (skip={skip}): conjunto vazio (404), nada a trazer")
                    continue  # resolvida: não há o que repescar
                ainda.append((page, skip, f"HTTPError {getattr(e.response, 'status_code', '?')}"))
            except Exception as e:
                ainda.append((page, skip, f"{type(e).__name__}: {e}"))
        pendentes = ainda

    for page, skip, motivo in sorted(pendentes):
        avisos.append(f"página {page} (skip={skip}) NÃO recuperada em {tentativas} tentativas: {motivo}")
    return registros, pendentes, avisos


def coletar_faturas(
    ts: TokenSession,
    cliente_ids: list[int],
    *,
    page_size: int = 180,
    max_workers: int = 8,
    atualizadas_desde: str | None = None,
) -> tuple[list, list[str]]:
    """Coleta as faturas dos clientes informados (``GET /faturas``).

    Args:
        ts: sessão autenticada.
        cliente_ids: clientes da carteira (mesmo escopo de ``listar_instalacoes``).
        page_size: ``limit`` por página (API: máx 2000).
        max_workers: threads paralelas sobre as páginas.
        atualizadas_desde: corte inclusivo em ``data_atualizacao``, que pega tanto fatura
            nova quanto reprocessada. ``None`` = snapshot completo.

    Returns:
        ``(registros, avisos)`` — registros crus de ``/faturas``, deduplicados pelo ``id``
        da fatura; ``avisos`` traz suspeita de filtro ignorado ou offset misturado.

    Raises:
        ColetaFaturasIncompleta: se alguma página não voltar nem na repescagem. Falhar é
            deliberado — ver a docstring da exceção.
    """
    if not cliente_ids:
        return [], []

    url = f'{ts.base_url}/faturas'
    headers = {'Fatture-SearchFields': ', '.join(CAMPOS_FATURA)}
    params: dict[str, object] = {'cliente_id': ','.join(map(str, cliente_ids))}
    if atualizadas_desde:
        params['data_atualizacao_inicio'] = atualizadas_desde

    corte = (
        f"atualizadas desde {atualizadas_desde}" if atualizadas_desde else "snapshot completo"
    )
    print(f"Faturas: clientes {params['cliente_id']} | {corte}")

    registros, falhas, total = coletar_paginado(
        ts,
        url,
        params,
        page_size=page_size,
        max_workers=max_workers,
        descricao="faturas",
        headers=headers,
    )

    avisos: list[str] = []
    if falhas:
        recuperados, restantes, avisos_repesca = _repescar(
            ts, url, params, headers, falhas, page_size=page_size
        )
        registros.extend(recuperados)
        avisos.extend(avisos_repesca)
        if restantes:
            raise ColetaFaturasIncompleta(
                f"{len(restantes)} de {-(-total // page_size)} página(s) de /faturas não "
                f"voltaram; a carga foi abortada para não gravar tabela com buraco. "
                f"Detalhe: {'; '.join(avisos_repesca)}"
            )

    # Dedupe defensivo pelo id da fatura: as páginas são pedidas em paralelo a partir do
    # total, então uma inserção no meio da varredura poderia deslocar uma linha e repeti-la.
    unicos: dict = {}
    for reg in registros:
        unicos.setdefault(reg.get('id'), reg)
    if len(unicos) != len(registros):
        print(f"[!] faturas: {len(registros) - len(unicos)} linha(s) duplicada(s) removida(s)")

    finais = list(unicos.values())
    if atualizadas_desde and finais:
        avisos.extend(_conferir_corte(finais, atualizadas_desde))
    return finais, avisos


def montar_tabela_faturas(registros: list) -> pd.DataFrame:
    """Monta o DataFrame nível fatura a partir dos registros de ``/faturas``.

    ``conteudo`` pode vir ``None`` (fatura sem conteúdo processado) — daí o encadeamento
    defensivo ``(x or {})`` em cada nível, que resolve para ``None`` na coluna.
    """
    linhas = []
    for reg in registros:
        conteudo = reg.get('conteudo') or {}
        unidade = conteudo.get('unidade_consumidora') or {}
        fatura = conteudo.get('fatura') or {}
        linhas.append({
            'id_fatura': reg.get('id'),
            'cliente_instalacao': unidade.get('nome'),
            'distribuidora_sigla': reg.get('distribuidora_sigla'),
            'id_instalacao': reg.get('instalacao_id'),
            'origem': conteudo.get('fatura_origem'),
            'data_emissao': fatura.get('data_emissao'),
            'data_vencimento': fatura.get('data_vencimento'),
            'data_criacao': reg.get('data_criacao'),
            'data_atualizacao': reg.get('data_atualizacao'),
        })
    return pd.DataFrame(linhas, columns=COLUNAS_SAIDA)


def combinar(existente: pd.DataFrame | None, coletado: pd.DataFrame) -> pd.DataFrame:
    """Upsert por ``id_fatura``: o que veio da API vence o que estava gravado.

    Vence porque a linha pode ter sido **reprocessada** — mesmo ``id``, conteúdo novo. Um
    "insere só o que falta" manteria a versão velha para sempre.

    A combinação é feita em pandas e a tabela é reescrita inteira (create-or-replace), em
    vez de ``MERGE`` no BigQuery. É a escolha certa nesta escala — ~2 mil linhas, crescendo
    poucas por dia — e mantém a lógica testável sem BigQuery. Se a tabela chegar à ordem de
    centenas de milhares de linhas, trocar por staging + ``MERGE ON id_fatura``.

    Returns:
        A tabela completa a gravar, ordenada por ``data_criacao`` desc.
    """
    if existente is None or existente.empty:
        base = coletado
    elif coletado.empty:
        base = existente
    else:
        atualizados = set(coletado['id_fatura'])
        mantidos = existente.loc[~existente['id_fatura'].isin(atualizados)]
        substituidas = len(existente) - len(mantidos)
        if substituidas:
            print(f"Faturas: {substituidas} linha(s) substituída(s) pela versão da API")
        print(f"Faturas: {len(coletado) - substituidas} linha(s) nova(s)")
        base = pd.concat([mantidos, coletado], ignore_index=True)

    return (
        base.reindex(columns=COLUNAS_SAIDA)
        .sort_values('data_criacao', ascending=False, na_position='last')
        .reset_index(drop=True)
    )
