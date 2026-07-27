"""Enriquecimento: adiciona a maior ``data_fim`` de execução do webcrawler.

Só as instalações que NÃO estão em sucesso (``status_webcrawler_id != STATUS_SUCESSO_ID``)
são consultadas em ``GET /webcrawlers/execucoes/``.

Estratégia por CHUNKS (evita estourar o tamanho da URL): os ``instalacao_id`` são
fatiados em blocos de ``WEBCRAWLER_CHUNK_SIZE`` (default 20). Cada bloco vira uma
consulta ``?instalacao_id=<csv de até 20>&sort=data_fim&order=desc``. Como vem
ordenado por ``data_fim`` desc, a 1ª ocorrência de cada ``instalacao_id`` já é a
última tentativa. Para não truncar, paginamos DENTRO do bloco (``skip``/``limit``)
até ter visto todos os ids do bloco ao menos uma vez (ou esgotarem as páginas) —
assim um bloco que voltasse exatamente no limite não perde instalação. Os blocos
rodam em paralelo (um future por bloco); a paginação dentro do bloco é sequencial
(evita aninhar pools de threads).
"""

from __future__ import annotations

import concurrent.futures

import pandas as pd

from src.fattureweb.session import TokenSession


def _buscar_bloco(
    ts: TokenSession,
    url: str,
    ids_bloco: list,
    *,
    page_size: int,
) -> tuple[list, dict[str, str], list[str]]:
    """Busca a última ``data_fim`` de cada instalação de um bloco (≤ chunk).

    Pagina em ``data_fim`` desc até ter visto todos os ids do bloco (a 1ª vista
    em desc é a mais recente) ou esgotar as páginas. Retorna
    ``(ids_bloco, {instalacao_id: data_fim}, avisos)``.
    """
    csv = ','.join(map(str, ids_bloco))
    base = {'instalacao_id': csv, 'sort': 'data_fim', 'order': 'desc'}
    alvo = {str(i) for i in ids_bloco}

    latest: dict[str, str] = {}
    avisos: list[str] = []
    skip = 0

    while alvo - latest.keys():  # enquanto faltar ver algum id do bloco
        data = ts.request('GET', url, params={**base, 'limit': page_size, 'skip': skip}).json()
        if data.get('status') != 'sucesso':
            avisos.append(f"bloco {csv}: status={data.get('status')!r} msg={data.get('mensagem')!r}")
            break
        linhas = data.get('dados') or []
        if not linhas:
            break  # acabaram as execuções; ids não vistos ficam sem data_fim

        for ex in linhas:
            iid = ex.get('instalacao_id')
            data_fim = ex.get('data_fim')
            if iid is None or not data_fim:
                continue
            chave = str(iid)
            # 1ª vista em desc = mais recente; comparo mesmo assim por robustez.
            if chave not in latest or data_fim > latest[chave]:
                latest[chave] = data_fim

        if len(linhas) < page_size:
            break  # última página do bloco -> não há truncamento
        skip += page_size

    return ids_bloco, latest, avisos


def enriquecer_com_data_fim(
    ts: TokenSession,
    df: pd.DataFrame,
    *,
    page_size: int = 180,
    max_workers: int = 8,
    status_sucesso_id: int = 10,
    chunk_size: int = 20,
) -> tuple[pd.DataFrame, list]:
    """Adiciona a coluna ``data_fim`` (maior data de execução do crawler).

    Args:
        ts: sessão autenticada.
        df: tabela base (precisa de ``id_instalacao`` e ``status_webcrawler_id``).
        page_size: ``limit`` na paginação dentro de cada bloco.
        max_workers: threads paralelas sobre os BLOCOS.
        status_sucesso_id: status considerado sucesso (não é consultado).
        chunk_size: nº de ``instalacao_id`` por chamada (teto do CSV na URL).

    Returns:
        ``(df, avisos)`` — ``df`` com a coluna ``data_fim``; ``avisos`` lista os
        blocos que falharam/deram status != sucesso.
    """
    df = df.copy()

    # Só as instalações que falharam (status != sucesso) são consultadas.
    falhou = df['status_webcrawler_id'] != status_sucesso_id
    ids = [i for i in df.loc[falhou, 'id_instalacao'].tolist() if i is not None]
    ids = list(dict.fromkeys(ids))  # únicos, preservando ordem

    if not ids:
        df['data_fim'] = None
        return df, []

    url = f'{ts.base_url}/webcrawlers/execucoes/'
    blocos = [ids[i:i + chunk_size] for i in range(0, len(ids), chunk_size)]
    print(f"Enriquecimento: {len(ids)} instalações em {len(blocos)} bloco(s) de até {chunk_size}")

    maior_data_fim: dict[str, str] = {}
    avisos: list[str] = []

    # Paralelo sobre os BLOCOS (paginação interna de cada bloco é sequencial).
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futuros = [ex.submit(_buscar_bloco, ts, url, bloco, page_size=page_size) for bloco in blocos]
        for fut in concurrent.futures.as_completed(futuros):
            try:
                _ids, latest, bloco_avisos = fut.result()
            except Exception as e:  # não derruba o todo
                avisos.append(f"bloco falhou: {type(e).__name__}: {e}")
                continue
            avisos.extend(bloco_avisos)
            # Acumula (merge) os blocos, mantendo a maior data_fim por instalação.
            for chave, data_fim in latest.items():
                if chave not in maior_data_fim or data_fim > maior_data_fim[chave]:
                    maior_data_fim[chave] = data_fim

    # Join na tabela principal por id_instalacao (status==10 não consultado -> None).
    df['data_fim'] = df['id_instalacao'].map(
        lambda x: maior_data_fim.get(str(x)) if pd.notna(x) else None
    )
    return df, avisos
