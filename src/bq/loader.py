"""Carga no BigQuery — as duas tabelas do monitor.

Mesmo projeto e dataset, variando só o nome da tabela. As duas são **create-or-replace**,
por motivos diferentes:
  - ``BQ_TABLE`` — status por instalação: é um retrato do agora, não tem histórico a
    preservar;
  - ``BQ_TABLE_FATURAS`` — nível fatura: é acumulativa, mas a carga é um **upsert** feito em
    pandas (``pipeline.faturas.combinar`` junta o gravado com o coletado, e a versão da API
    vence) e o resultado substitui a tabela. Não é ``append`` porque uma fatura
    reprocessada tem de sobrescrever a linha antiga, não virar uma segunda linha.

Caminho escolhido: ``pandas_gbq.to_gbq`` — uma linha para materializar o DataFrame,
alinhado à stack da skill ``engenharia-dados-matrix`` (``pandas_gbq``). Auth por ADC (sem
key file): local via ``gcloud auth application-default login``; no Cloud Run, a service
account do Job.
"""

from __future__ import annotations

import pandas as pd

from src.config import settings


def _carregar(df: pd.DataFrame, tabela: str, *, if_exists: str = "replace") -> str:
    """Materializa o DataFrame em ``GCP_PROJECT.BQ_DATASET.<tabela>``.

    Args:
        df: tabela a gravar.
        tabela: nome da tabela no dataset.
        if_exists: ``"replace"`` (create-or-replace, WRITE_TRUNCATE) ou ``"append"``.

    Returns:
        O nome totalmente qualificado da tabela de destino.
    """
    import pandas_gbq  # lazy: só exige a dep quando de fato carrega

    destino = f"{settings.BQ_DATASET}.{tabela}"
    pandas_gbq.to_gbq(
        df,
        destination_table=destino,
        project_id=settings.GCP_PROJECT,
        if_exists=if_exists,
    )
    return f"{settings.GCP_PROJECT}.{destino}"


def carregar_status_webcrawler(df: pd.DataFrame) -> str:
    """Carrega a tabela de status por instalação (``BQ_TABLE``), create-or-replace."""
    return _carregar(df, settings.BQ_TABLE, if_exists="replace")


def carregar_faturas(df: pd.DataFrame) -> str:
    """Carrega a tabela nível fatura (``BQ_TABLE_FATURAS``), create-or-replace.

    Espera receber a tabela **completa** — o resultado de
    ``pipeline.faturas.combinar(gravado, coletado)``, não só o lote novo.
    """
    return _carregar(df, settings.BQ_TABLE_FATURAS, if_exists="replace")
