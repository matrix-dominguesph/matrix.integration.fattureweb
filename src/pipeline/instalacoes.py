"""Coleta paginada de instalações do Fattureweb.

Expõe um coletor paginado genérico (``coletar_paginado``, reutilizado pelo
enriquecimento de webcrawler) e ``listar_instalacoes`` (o filtro por cliente).
Recebe a ``TokenSession`` por injeção — não instancia sessão nem lê settings de
transporte aqui.
"""

from __future__ import annotations

import concurrent.futures
import math
from typing import Any, Optional

from src.fattureweb.session import TokenSession
from src.pipeline.tabela import CAMPOS_INSTALACAO


def _extrair_total(resposta_json: dict) -> int:
    """Total de registros a partir da resposta de ``count=true``.

    ASSUNÇÃO (padrão do ``client.py`` do motor GD: ``dados[0]['total']``).
    TODO: validar na 1ª execução — ``count=true`` pode devolver o total em outro
    shape (chave ``total`` na raiz, em ``mensagem``, ou noutra posição de
    ``dados``); ajustar aqui se necessário.
    """
    dados = resposta_json.get('dados') or []
    if dados and isinstance(dados[0], dict) and 'total' in dados[0]:
        return int(dados[0]['total'])
    if 'total' in resposta_json:  # fallback defensivo
        return int(resposta_json['total'])
    raise ValueError(f"Não localizei o total na resposta de count: {resposta_json!r}")


def coletar_paginado(
    ts: TokenSession,
    url: str,
    params_base: dict[str, Any],
    *,
    page_size: int,
    max_workers: int,
    descricao: str = "registros",
    headers: Optional[dict] = None,
) -> tuple[list, list, int]:
    """Coleta todos os registros de ``url`` paginando em paralelo.

    Descobre o total via ``count=true``, calcula as páginas e busca cada uma
    (``limit``/``skip``) em threads. Agrega preservando a ordem por ``skip`` e
    coleta falhas por página (sem derrubar o todo).

    Args:
        ts: sessão autenticada.
        url: endpoint completo (ex.: ``{base_url}/instalacoes``).
        params_base: filtros fixos aplicados em TODAS as chamadas (count e páginas).
        page_size: ``limit`` por página (API: máx 2000).
        max_workers: threads paralelas (8 é modesto de propósito — GETs
            independentes; token só é lido, não reescrito).
        descricao: rótulo para os prints.
        headers: cabeçalhos extras por requisição (ex.: ``Fatture-SearchFields``
            para escolher a projeção de campos do registro).

    Returns:
        ``(registros, falhas, total)`` — ``registros`` na ordem por ``skip``;
        ``falhas`` = lista de ``(page, skip, motivo)``.
    """
    req_headers = dict(headers) if headers else None

    # count='true' (string) espelha o literal `&count=true` do motor; um bool
    # True viraria 'True' na query e a API poderia não reconhecer.
    count_json = ts.request(
        'GET', url, params={**params_base, 'count': 'true'}, headers=req_headers
    ).json()
    total = _extrair_total(count_json)
    n_paginas = math.ceil(total / page_size) if total else 0
    print(f"Total de {descricao}: {total} | páginas de {page_size}: {n_paginas}")

    def buscar_pagina(page: int) -> tuple[int, list, Optional[str]]:
        skip = page * page_size
        try:
            resp = ts.request(
                'GET', url,
                params={**params_base, 'limit': page_size, 'skip': skip},
                headers=dict(req_headers) if req_headers else None,
            )
            data = resp.json()
            if data.get('status') != 'sucesso':
                return page, [], f"status={data.get('status')!r} msg={data.get('mensagem')!r}"
            return page, (data.get('dados') or []), None
        except Exception as e:  # não derruba o todo
            return page, [], f"{type(e).__name__}: {e}"

    paginas: dict[int, list] = {}
    falhas: list[tuple[int, int, str]] = []  # (page, skip, motivo)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futuros = [ex.submit(buscar_pagina, p) for p in range(n_paginas)]
        for fut in concurrent.futures.as_completed(futuros):  # voltam fora de ordem
            page, linhas, erro = fut.result()
            if erro:
                falhas.append((page, page * page_size, erro))
            else:
                paginas[page] = linhas

    registros = [linha for p in sorted(paginas) for linha in paginas[p]]
    return registros, falhas, total


def listar_instalacoes(
    ts: TokenSession,
    cliente_ids: list[int],
    page_size: int = 180,
    max_workers: int = 8,
) -> tuple[list, list]:
    """Lista as instalações dos clientes informados (``GET /instalacoes``).

    Filtra por ``cliente_id`` (lista unida por vírgula). Retorna
    ``(instalacoes, falhas)``.
    """
    url = f'{ts.base_url}/instalacoes'
    cliente_id_csv = ','.join(map(str, cliente_ids))
    # Sem Fatture-SearchFields a API devolve só a projeção padrão (basicamente o
    # `id`) e os demais campos vêm ausentes -> None na tabela. Pedimos exatamente
    # os campos que o montar_tabela extrai (fonte única em tabela.CAMPOS_INSTALACAO).
    headers = {'Fatture-SearchFields': ', '.join(CAMPOS_INSTALACAO)}
    instalacoes, falhas, _ = coletar_paginado(
        ts,
        url,
        {'cliente_id': cliente_id_csv},
        page_size=page_size,
        max_workers=max_workers,
        descricao="instalações",
        headers=headers,
    )
    return instalacoes, falhas
