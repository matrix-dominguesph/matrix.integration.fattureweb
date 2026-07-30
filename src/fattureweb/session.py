"""Sessão autenticada do Fattureweb — transporte puro.

Só autenticação e ``request`` genérico (com re-login em 401). NÃO conhece endpoints de
negócio: quem sabe de ``/instalacoes``, ``/webcrawlers`` ou ``/faturas`` são os módulos de
``src.pipeline``, que recebem uma ``TokenSession`` por injeção. Isso mantém esta camada
reusável (e alinhável ao ``client.py`` do motor GD).

Reuso de conexão e retry
------------------------
Toda requisição passa por uma única ``requests.Session``, com ``HTTPAdapter`` configurado.
Antes o código usava ``requests.request(...)`` (a função de módulo), que abre uma conexão,
faz o handshake TLS, usa uma vez e descarta — numa varredura de 54 páginas eram 54
handshakes, todos atravessando a inspeção TLS da rede corporativa. Duas consequências da
troca:

  - **reuso de conexão**: medido, 212 requisições em 9,8s contra 14,5s (~1,5x);
  - **retry no nível da conexão**: o ``urllib3`` repete a MESMA requisição sozinho, antes de
    a exceção chegar ao código de negócio. Vale para **todas** as etapas do pipeline
    (instalações, webcrawler, faturas e o próprio login), em vez de cada uma precisar do seu.

O que **não** é repetido, de propósito:

  - **401** — quem trata é o ``request`` aqui, refazendo o login e repetindo uma vez;
  - **404** — neste API significa "conjunto vazio" (ver ``pipeline.faturas``), não falha;
  - **4xx em geral** — pedido malformado não melhora com insistência.

Isso reduz a frequência da falha, mas não substitui o guarda-corpo da camada de pipeline
(``ColetaFaturasIncompleta``): o transporte não tem como decidir "não gravar tabela com
buraco". Os dois convivem — retry aqui, decisão de não gravar parcial lá.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter, Retry

from src.config import settings

# Tentativas do urllib3 por requisição, e espera entre elas (0.5s, 1s, 2s, 4s...).
TENTATIVAS_TRANSPORTE = 5
BACKOFF_TRANSPORTE = 0.5
# Status que valem repetir: sobrecarga e indisponibilidade temporária do servidor.
STATUS_REPETIVEIS = (429, 500, 502, 503, 504)


def _montar_sessao() -> requests.Session:
    """Uma ``requests.Session`` com pool de conexões e retry configurados."""
    retry = Retry(
        total=TENTATIVAS_TRANSPORTE,
        connect=TENTATIVAS_TRANSPORTE,
        read=TENTATIVAS_TRANSPORTE,
        backoff_factor=BACKOFF_TRANSPORTE,
        status_forcelist=STATUS_REPETIVEIS,
        # POST entra porque o único POST daqui é o /auth/login, que é idempotente
        # (devolve um token novo). Sem isso, uma queda de conexão no login não é repetida.
        allowed_methods=frozenset(['GET', 'POST']),
        raise_on_status=False,  # 4xx/5xx voltam como resposta; quem decide é o request()
    )
    # O pool acompanha MAX_WORKERS: menor que isso, o urllib3 avisa "pool is full" e
    # descarta conexão, desfazendo justamente o reuso que queremos.
    adaptador = HTTPAdapter(
        pool_connections=max(10, settings.MAX_WORKERS),
        pool_maxsize=max(10, settings.MAX_WORKERS),
        max_retries=retry,
    )
    sessao = requests.Session()
    sessao.mount('https://', adaptador)
    sessao.mount('http://', adaptador)
    return sessao


class TokenSession:
    """Gerencia login e requisições autenticadas na API do Fattureweb.

    A ``requests.Session`` interna é compartilhada pelas threads do pipeline. É seguro
    porque o pool de conexões do ``urllib3`` é thread-safe e nada aqui muta estado da sessão
    por requisição — os headers vão por chamada, não em ``sessao.headers``.
    """

    def __init__(self):
        self.base_url = settings.FATTUREWEB_BASE_URL
        self.login_path = f'{self.base_url}/auth/login'
        self.login_payload = {
            'email': settings.FATTUREWEB_USERNAME,
            'senha': settings.FATTUREWEB_PASSWORD,
        }
        self.token = None
        self.sessao = _montar_sessao()

    def login(self):
        """Autentica e guarda o token; lança se as credenciais falharem."""
        response = self.sessao.post(self.login_path, json=self.login_payload)
        response.raise_for_status()
        json_data = response.json()

        if json_data.get('status') != 'sucesso':
            raise Exception(f"Login failed: {json_data.get('mensagem')}")

        self.token = json_data['dados'][0]['token']

    def request(self, method: str, path: str, **kwargs):
        """Requisição autenticada; renova o token e repete uma vez em 401.

        Falha de conexão e 5xx já foram repetidos pelo transporte antes de chegar aqui
        (ver ``_montar_sessao``); o que sobe como exceção é o que não se recuperou.
        """
        if not self.token:
            self.login()

        headers = dict(kwargs.pop('headers', None) or {})  # tolera headers=None
        headers['Fatture-AuthToken'] = self.token
        kwargs['headers'] = headers

        response = self.sessao.request(method, path, **kwargs)

        if response.status_code == 401:
            self.login()
            headers['Fatture-AuthToken'] = self.token
            kwargs['headers'] = headers
            response = self.sessao.request(method, path, **kwargs)

        response.raise_for_status()
        return response
