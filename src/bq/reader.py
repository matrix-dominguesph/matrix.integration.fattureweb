"""Leitura do BigQuery — o estado da tabela nível fatura, que decide o modo da execução.

A tabela gravada é a única fonte do corte incremental: o ``MAX(data_criacao)`` dela vira o
``data_atualizacao_inicio`` da consulta a ``/faturas`` (o porquê desse par está no topo de
``pipeline.faturas``). E a tabela inteira é lida porque a carga é um **upsert**: a
combinação do gravado com o coletado é feita em pandas e a tabela é reescrita.

Ler a tabela toda só é razoável na escala atual (~2 mil linhas, poucas novas por dia). Se
crescer para centenas de milhares, trocar por staging + ``MERGE ON id_fatura`` e aí ler
apenas o ``MAX(data_criacao)``.

As datas são STRING na tabela (a camada raw guarda o que a API devolveu). ``MAX()`` sobre
STRING é lexicográfico — o que coincide com o máximo cronológico porque o formato é ISO-8601
de largura fixa e o offset é sempre ``-03:00`` (conferido em 1962 linhas). E tem uma
vantagem: o valor sai pronto para ir como filtro, sem conversão. Se algum dia aparecer
offset diferente, o ``_conferir_corte`` em ``pipeline.faturas`` avisa.
"""

from __future__ import annotations

import pandas as pd

from src.config import settings
from src.pipeline.faturas import COLUNAS_SAIDA


def _tabela_faturas() -> str:
    return f"{settings.GCP_PROJECT}.{settings.BQ_DATASET}.{settings.BQ_TABLE_FATURAS}"


def _ler(query: str) -> pd.DataFrame:
    """Roda a query e devolve um DataFrame (auth por ADC, igual ao loader)."""
    import pandas_gbq  # lazy: só exige a dep quando de fato lê

    return pandas_gbq.read_gbq(query, project_id=settings.GCP_PROJECT)


def tabela_faturas_existe() -> bool:
    """A tabela nível fatura já existe no dataset?

    Consulta ``INFORMATION_SCHEMA`` em vez de tentar ler e capturar exceção — assim a
    ausência da tabela é uma resposta, não um erro a adivinhar pelo tipo.
    """
    query = f"""
        SELECT COUNT(*) AS n
        FROM `{settings.GCP_PROJECT}.{settings.BQ_DATASET}.INFORMATION_SCHEMA.TABLES`
        WHERE table_name = '{settings.BQ_TABLE_FATURAS}'
    """
    return int(_ler(query)['n'].iloc[0]) > 0


def ler_faturas() -> pd.DataFrame | None:
    """A tabela nível fatura como está gravada hoje.

    Returns:
        O DataFrame com as colunas de ``COLUNAS_SAIDA``, ou ``None`` se a tabela ainda não
        existe (o que o ``main`` lê como "primeira execução -> snapshot completo").
    """
    if not tabela_faturas_existe():
        print("Faturas: tabela ainda não existe -> snapshot completo.")
        return None

    df = _ler(f"SELECT {', '.join(COLUNAS_SAIDA)} FROM `{_tabela_faturas()}`")
    if df.empty:
        print("Faturas: tabela existe mas está vazia -> snapshot completo.")
        return None

    print(f"Faturas: {len(df)} linha(s) já gravada(s).")
    return df


def corte_incremental(existente: pd.DataFrame | None) -> str | None:
    """O corte a mandar para a API: ``MAX(data_criacao)`` do que já está gravado.

    Vai como ``data_atualizacao_inicio``, e não como ``data_inicio``, para pegar também as
    faturas **reprocessadas** (mesma ``data_criacao``, ``data_atualizacao`` nova). Como
    ``MAX(data_criacao) <= MAX(data_atualizacao)``, o corte sobra um pouco para trás — a
    sobreposição é de graça e o upsert a absorve.
    """
    if existente is None or existente.empty:
        return None
    corte = str(existente['data_criacao'].max())
    print(f"Faturas: corte data_atualizacao >= {corte} (MAX(data_criacao) da tabela).")
    return corte
