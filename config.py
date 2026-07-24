"""Configuração via variáveis de ambiente (Pydantic Settings).

Apenas os campos usados pelo extrator. Segredos SEMPRE via env — nunca hardcode.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Credenciais do FattureWeb (via env; nunca commitadas) ---------------
    FATTUREWEB_BASE_URL: str = ""
    FATTUREWEB_USERNAME: str = ""
    FATTUREWEB_PASSWORD: str = ""


settings = Settings()
