"""Carga no BigQuery — create-or-replace da tabela de status do webcrawler.

Caminho escolhido: ``pandas_gbq.to_gbq(..., if_exists="replace")`` — uma linha
para materializar o DataFrame como create-or-replace, alinhado à stack da skill
``engenharia-dados-matrix`` (``pandas_gbq``). Auth por ADC (sem key file):
local via ``gcloud auth application-default login``; no Cloud Run, a service
account do Job.
"""

from __future__ import annotations

import pandas as pd

from src.config import settings


def carregar_status_webcrawler(df: pd.DataFrame) -> str:
    """Sobe o DataFrame como create-or-replace em ``GCP_PROJECT.DATASET.TABLE``.

    Returns:
        O nome totalmente qualificado da tabela de destino.
    """
    import pandas_gbq  # lazy: só exige a dep quando de fato carrega

    destino = f"{settings.BQ_DATASET}.{settings.BQ_TABLE}"
    pandas_gbq.to_gbq(
        df,
        destination_table=destino,
        project_id=settings.GCP_PROJECT,
        if_exists="replace",  # create-or-replace idempotente (WRITE_TRUNCATE)
    )
    return f"{settings.GCP_PROJECT}.{destino}"
