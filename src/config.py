"""Configuração via variáveis de ambiente (Pydantic Settings).

Os campos usados pelo pipeline. Segredos SEMPRE via env — nunca hardcode.
Qualquer campo pode ser sobrescrito por variável de ambiente de mesmo nome
(carteiras/tuning/destino BQ), sem tocar código.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Credenciais do FattureWeb (via env; nunca commitadas) ---------------
    FATTUREWEB_BASE_URL: str = ""
    FATTUREWEB_USERNAME: str = ""
    FATTUREWEB_PASSWORD: str = ""

    # --- Destino BigQuery (create-or-replace; auth via ADC) ------------------
    GCP_PROJECT: str = ""
    BQ_DATASET: str = ""
    BQ_TABLE: str = ""

    # --- Tuning do pipeline --------------------------------------------------
    PAGE_SIZE: int = 180
    MAX_WORKERS: int = 8
    STATUS_SUCESSO_ID: int = 10  # status_webcrawler_id de sucesso (não enriquece)
    # Enriquecimento webcrawler: nº de instalacao_id por chamada. Limita o tamanho
    # da querystring — um CSV com TODOS os ids de uma vez estoura a URL.
    WEBCRAWLER_CHUNK_SIZE: int = 20

    # --- Escopo de coleta ----------------------------------------------------
    # Clientes da carteira 2768 ("MATRIX FÁCIL B"). Estender a outras carteiras
    # é mudar esta lista por env (JSON), sem alterar código.
    CLIENTE_IDS: list[int] = [208062, 208063]


settings = Settings()
