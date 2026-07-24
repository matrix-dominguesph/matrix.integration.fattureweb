"""Lista as instalações do FattureWeb filtradas pelos clientes de uma carteira.

Ex.: carteira "MATRIX FÁCIL B" (id 2768) → clientes 208062 e 208063 →
instalações via ``GET /instalacoes?cliente_id=208062,208063``.

Autocontido e rodável a partir da RAIZ do repo (o ``from config import settings``
só resolve com o CWD na raiz, onde vive o ``config.py``).

Uso:
    python listar_clientes_carteira.py
"""

from __future__ import annotations

# --- Bootstrap TLS -----------------------------------------------------------
# Rede corporativa faz inspeção TLS: sem o trust store do SO, o ``requests``
# falha no handshake com CERTIFICATE_VERIFY_FAILED (unable to get local issuer
# certificate). ``truststore`` usa o store do SO (que tem a CA raiz interna).
# Best-effort: se não estiver instalado, segue (e aí depende do ambiente).
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import concurrent.futures
import math
from typing import Optional

import pandas as pd  # dependência de fato — incluir no requirements.txt (quando criar)
import requests

from config import settings


# Carteira alvo (contexto). A API de instalações filtra por cliente_id, então
# usamos os clientes da carteira. CARTEIRA 2768 ("MATRIX FÁCIL B") -> estes ids.
CARTEIRA_ID = 2768  # "MATRIX FÁCIL B"
CLIENTE_IDS = [208062, 208063]  # clientes da carteira 2768 (MATRIX FÁCIL B)


class TokenSession:
    """Gerenciador de sessão autenticada para a API do FattureWeb.

    Copiado da versão salva do ``execucao.ipynb`` para manter o arquivo
    autocontido. TODO (futuro): extrair para um módulo próprio / alinhar ao
    ``client.py`` do motor (`matrix.gd.motor.calculo`), que segue o mesmo
    contrato de login mas usa outros nomes de env (FATTUREWEB_EMAIL/SENHA).
    """

    def __init__(self):
        self.base_url = settings.FATTUREWEB_BASE_URL
        self.login_path = f'{self.base_url}/auth/login'
        self.login_payload = {
            'email': settings.FATTUREWEB_USERNAME,
            'senha': settings.FATTUREWEB_PASSWORD
        }
        self.token = None

    def login(self):
        response = requests.post(self.login_path, json=self.login_payload)
        response.raise_for_status()
        json_data = response.json()

        if json_data.get('status') != 'sucesso':
            raise Exception(f"Login failed: {json_data.get('mensagem')}")

        self.token = json_data['dados'][0]['token']

    def request(self, method: str, path: str, **kwargs):
        if not self.token:
            self.login()

        headers = kwargs.pop('headers', {})
        headers['Fatture-AuthToken'] = self.token
        kwargs['headers'] = headers

        response = requests.request(method, path, **kwargs)

        if response.status_code == 401:
            self.login()
            headers['Fatture-AuthToken'] = self.token
            kwargs['headers'] = headers
            response = requests.request(method, path, **kwargs)

        response.raise_for_status()
        return response


def _extrair_total(resposta_json: dict) -> int:
    """Total de registros a partir da resposta de ``count=true``.

    ASSUNÇÃO (padrão do ``client.py`` do motor, em ``obter_fatura_ids_usinas``:
    ``dados[0]['total']``). TODO: validar na 1ª execução — o ``count=true`` de
    ``/instalacoes`` pode devolver o total num shape diferente (chave ``total``
    na raiz, em ``mensagem``, ou noutra posição de ``dados``); ajustar aqui.
    """
    dados = resposta_json.get('dados') or []
    if dados and isinstance(dados[0], dict) and 'total' in dados[0]:
        return int(dados[0]['total'])
    if 'total' in resposta_json:  # fallback defensivo
        return int(resposta_json['total'])
    raise ValueError(f"Não localizei o total na resposta de count: {resposta_json!r}")


def listar_instalacoes(
    ts: TokenSession,
    cliente_ids: list[int],
    page_size: int = 180,
    max_workers: int = 8,
) -> tuple[list, list]:
    """Lista as instalações dos clientes informados, paginando em paralelo.

    Args:
        ts: sessão autenticada (``ts.login()`` já deve ter sido chamado).
        cliente_ids: ids de cliente para filtrar (ex.: os clientes da carteira).
        page_size: registros por página (``limit``; API: máx 2000, default 100).
        max_workers: threads paralelas. 8 é modesto de propósito — as páginas são
            GETs independentes; equilibra throughput x educação com a API (evita
            rajada/rate-limit). O token é só LIDO pelas threads (não reescrito).

    Returns:
        ``(instalacoes, falhas)`` — ``instalacoes`` na ordem por ``skip``;
        ``falhas`` é uma lista de ``(page, skip, motivo)`` das páginas que não vieram.
    """
    # Endpoint /instalacoes filtrando por cliente_id (lista unida por vírgula).
    # O filtro vai em TODAS as chamadas: count e cada página.
    url = f'{ts.base_url}/instalacoes'
    cliente_id_csv = ','.join(map(str, cliente_ids))

    # count='true' (string) espelha o literal `&count=true` do motor; um bool
    # True viraria 'True' na query e a API poderia não reconhecer.
    count_json = ts.request('GET', url, params={'cliente_id': cliente_id_csv, 'count': 'true'}).json()
    total = _extrair_total(count_json)
    n_paginas = math.ceil(total / page_size) if total else 0
    print(f"Total de instalações: {total} | páginas de {page_size}: {n_paginas}")

    def buscar_pagina(page: int) -> tuple[int, list, Optional[str]]:
        """Busca uma página (skip = page * page_size). Retorna (page, linhas, erro)."""
        skip = page * page_size
        try:
            resp = ts.request('GET', url, params={'cliente_id': cliente_id_csv, 'limit': page_size, 'skip': skip})
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

    # Agrega preservando a ordem por página/skip.
    instalacoes = [linha for p in sorted(paginas) for linha in paginas[p]]
    return instalacoes, falhas


# Colunas da tabela: nome de saída -> chave no registro de /instalacoes.
# O status do último crawler já vem na própria resposta de /instalacoes
# (não precisa chamar /webcrawlers/execucoes).
_COLUNAS = {
    'id_instalacao': 'id',
    'cliente_apelido': 'cliente_apelido',
    'distribuidora_sigla': 'distribuidora_sigla',
    'status_webcrawler_id': 'status_webcrawlers_id',
    'erro_processamento': 'erro_processamento',
    'descricao_status_webcrawler': 'descricao_status_webcrawler',
}


def montar_tabela(instalacoes: list) -> pd.DataFrame:
    """Monta a tabela de instalações + status do crawler direto da resposta.

    Usa ``.get(...)`` por campo para tolerar registro sem a chave (-> None).
    """
    linhas = [
        {saida: inst.get(chave) for saida, chave in _COLUNAS.items()}
        for inst in instalacoes
    ]
    return pd.DataFrame(linhas, columns=list(_COLUNAS))


if __name__ == "__main__":
    ts = TokenSession()
    ts.login()  # login single-thread ANTES do paralelo -> evita re-login concorrente

    # CARTEIRA_ID (2768) -> CLIENTE_IDS -> instalações desses clientes.
    instalacoes, falhas = listar_instalacoes(ts, CLIENTE_IDS)

    df = montar_tabela(instalacoes)

    print(f"Instalações coletadas: {len(instalacoes)}")
    if falhas:
        print(f"[!] {len(falhas)} página(s) falharam (reexecutar só estas):")
        for page, skip, motivo in sorted(falhas):
            print(f"    página {page} (skip={skip}): {motivo}")

    print(df.to_string(index=False))

