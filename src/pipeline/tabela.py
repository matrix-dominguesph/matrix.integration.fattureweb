"""Modelagem da tabela de saída a partir dos registros de ``/instalacoes``.

O status do último crawler já vem na própria resposta de ``/instalacoes`` — não
é preciso chamar ``/webcrawlers/execucoes`` para as 6 colunas base (a coluna
``data_fim`` é adicionada depois, no enriquecimento).
"""

from __future__ import annotations

import pandas as pd

# Colunas da tabela: nome de saída -> chave no registro de /instalacoes.
_COLUNAS = {
    'id_instalacao': 'id',
    'cliente_apelido': 'cliente_apelido',
    'distribuidora_sigla': 'distribuidora_sigla',
    'status_webcrawler_id': 'status_webcrawlers_id',
    'erro_processamento': 'erro_processamento',
    'descricao_status_webcrawler': 'descricao_status_webcrawler',
    # `status` do /instalacoes é a instalação estar ATIVA na carteira, e não tem relação
    # com o status do crawler — daí o nome de saída ser explícito. A API oferece os
    # endpoints Ativar/Inativar e o filtro `?status=true|false`; nós coletamos as duas e
    # marcamos, para não perder de vista a UC cancelada. Medido em 2026-07-31: 845 ativas
    # e 10 inativas nas 855 da carteira. Sem esta coluna, as 10 canceladas entravam no
    # denominador da cobertura como se fossem carteira ativa.
    'instalacao_ativa': 'status',
}

# Campos que o /instalacoes deve retornar (o header Fatture-SearchFields controla
# a projeção; sem ele a API devolve só um subconjunto e os demais vêm ausentes).
# Fonte única: exatamente as chaves de origem que o montar_tabela extrai.
CAMPOS_INSTALACAO = list(_COLUNAS.values())


def montar_tabela(instalacoes: list) -> pd.DataFrame:
    """Monta o DataFrame base (6 colunas) direto dos registros de instalação.

    Usa ``.get(...)`` por campo para tolerar registro sem a chave (-> None).
    """
    linhas = [
        {saida: inst.get(chave) for saida, chave in _COLUNAS.items()}
        for inst in instalacoes
    ]
    return pd.DataFrame(linhas, columns=list(_COLUNAS))
