"""Sessão autenticada do Fattureweb — transporte puro.

Só autenticação e ``request`` genérico (com re-login em 401). NÃO conhece
endpoints de negócio: quem sabe de ``/instalacoes`` ou ``/webcrawlers`` são os
módulos de ``src.pipeline``, que recebem uma ``TokenSession`` por injeção. Isso
mantém esta camada reusável (e alinhável ao ``client.py`` do motor GD).
"""

from __future__ import annotations

import requests

from src.config import settings


class TokenSession:
    """Gerencia login e requisições autenticadas na API do Fattureweb."""

    def __init__(self):
        self.base_url = settings.FATTUREWEB_BASE_URL
        self.login_path = f'{self.base_url}/auth/login'
        self.login_payload = {
            'email': settings.FATTUREWEB_USERNAME,
            'senha': settings.FATTUREWEB_PASSWORD,
        }
        self.token = None

    def login(self):
        """Autentica e guarda o token; lança se as credenciais falharem."""
        response = requests.post(self.login_path, json=self.login_payload)
        response.raise_for_status()
        json_data = response.json()

        if json_data.get('status') != 'sucesso':
            raise Exception(f"Login failed: {json_data.get('mensagem')}")

        self.token = json_data['dados'][0]['token']

    def request(self, method: str, path: str, **kwargs):
        """Requisição autenticada; renova o token e repete uma vez em 401."""
        if not self.token:
            self.login()

        headers = dict(kwargs.pop('headers', None) or {})  # tolera headers=None
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
