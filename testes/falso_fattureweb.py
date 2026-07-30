"""Dublê da API do Fattureweb, para os testes rodarem offline.

Imita o que a API real faz — inclusive as esquisitices que motivaram o código de produção,
cada uma verificada contra a API de verdade:

  - ``count=true`` responde **sempre 200**, com ``total: 0`` quando nada casa;
  - uma página de conjunto vazio responde **404**, não 200 com lista vazia;
  - ``data_atualizacao_inicio`` filtra ``data_atualizacao`` e é **inclusivo** (``>=``);
  - parâmetro desconhecido é **ignorado em silêncio** (simulável por
    ``ignora_filtro=True``).

E permite sabotar páginas específicas (``falhas_por_skip``) para exercitar a repescagem e o
aborto.
"""

from __future__ import annotations

import requests

FILTRO_INCREMENTAL = 'data_atualizacao_inicio'


class RespostaFalsa:
    """O mínimo que o código de produção usa de uma resposta ``requests``."""

    def __init__(self, corpo: dict, status_code: int = 200) -> None:
        self._corpo = corpo
        self.status_code = status_code

    def json(self) -> dict:
        return self._corpo


class FattureWebFalso:
    """Serve ``/faturas`` a partir de uma lista de registros em memória.

    Args:
        registros: os registros que a API "tem".
        falhas_por_skip: ``{skip: quantas vezes falhar}`` — simula queda de conexão.
        ignora_filtro: se ``True``, devolve tudo mesmo com o filtro incremental (é o que a
            API faz quando o nome do parâmetro não é reconhecido).
        total_extra: quanto o ``count`` infla o total além dos registros que de fato serve.
            Simula linhas removidas entre o ``count`` e a busca das páginas — a última
            página deixa de existir e responde 404.
    """

    def __init__(
        self,
        registros: list[dict],
        *,
        falhas_por_skip: dict[int, int] | None = None,
        ignora_filtro: bool = False,
        total_extra: int = 0,
    ) -> None:
        self.base_url = 'https://falso.fattureweb.test'
        self.token = 'token-falso'
        self.registros = registros
        self.falhas_restantes = dict(falhas_por_skip or {})
        self.ignora_filtro = ignora_filtro
        self.total_extra = total_extra
        self.chamadas: list[dict] = []  # histórico, para os testes conferirem

    def _filtrar(self, params: dict) -> list[dict]:
        corte = params.get(FILTRO_INCREMENTAL)
        if not corte or self.ignora_filtro:
            return self.registros
        # Inclusivo (>=) sobre data_atualizacao, como a doc especifica e a API confirma.
        return [r for r in self.registros if (r.get('data_atualizacao') or '') >= corte]

    def request(self, method: str, path: str, **kwargs):
        params = dict(kwargs.get('params') or {})
        self.chamadas.append(params)
        casadas = self._filtrar(params)

        if params.get('count') == 'true':
            # count nunca 404 — responde 200 com total 0.
            return RespostaFalsa({
                'status': 'sucesso',
                'mensagem': 'Faturas encontradas.',
                'dados': [{'total': len(casadas) + self.total_extra}],
            })

        skip = int(params.get('skip', 0))
        limit = int(params.get('limit', 180))

        restantes = self.falhas_restantes.get(skip, 0)
        if restantes > 0:
            self.falhas_restantes[skip] = restantes - 1
            raise requests.ConnectionError(f"falha de transporte simulada em skip={skip}")

        pagina = casadas[skip:skip + limit]
        if not pagina:
            # Conjunto vazio (ou skip além do fim) = 404, como a API real.
            resp = RespostaFalsa(
                {'status': 'erro', 'mensagem': 'Faturas não encontradas.'}, status_code=404
            )
            raise requests.HTTPError('404 Client Error: Not Found', response=resp)

        return RespostaFalsa({
            'status': 'sucesso',
            'mensagem': 'Faturas encontradas.',
            'dados': pagina,
        })


def registro(
    id_fatura: int,
    *,
    instalacao: int = 1000,
    dia: int = 1,
    dia_atualizacao: int | None = None,
    origem: str = 'SITE',
    distribuidora: str = 'ceal',
    nome: str = 'COOPERATIVA TERENAS ENERGIA',
    conteudo_nulo: bool = False,
) -> dict:
    """Um registro de ``/faturas`` no shape da API (projeção ``CAMPOS_FATURA``).

    ``dia_atualizacao`` maior que ``dia`` simula uma fatura **reprocessada**: mesma
    ``data_criacao``, ``data_atualizacao`` posterior.
    """
    criacao = f'2026-07-{dia:02d}T10:00:00.000000-03:00'
    atualizacao = (
        criacao if dia_atualizacao is None
        else f'2026-07-{dia_atualizacao:02d}T10:00:00.000000-03:00'
    )
    return {
        'id': id_fatura,
        'instalacao_id': instalacao,
        'distribuidora_sigla': distribuidora,
        'data_criacao': criacao,
        'data_atualizacao': atualizacao,
        'conteudo': None if conteudo_nulo else {
            'fatura_origem': origem,
            'unidade_consumidora': {'nome': nome},
            'fatura': {'data_emissao': f'2026-06-{dia:02d} 03:00:00+00:00'},
        },
    }
