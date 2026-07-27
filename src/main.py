"""Orquestração do monitor de erros do Fattureweb (Cloud Run Job).

Fluxo fim-a-fim (5 etapas):
  1. auth               — TokenSession.login()
  2. coleta             — listar_instalacoes (carteira -> clientes -> instalações)
  3. modelagem          — montar_tabela (6 colunas)
  4. enriquecimento     — maior data_fim do webcrawler p/ status != sucesso
  5. carga              — create-or-replace no BigQuery

Rodar (local ou container, a partir da raiz do repo):
    python -m src.main
"""

from __future__ import annotations

# --- Bootstrap de path -------------------------------------------------------
# Coloca a RAIZ do repo no sys.path para os imports absolutos ``from src....``
# funcionarem tanto com ``python -m src.main`` (canônico, usado no Docker com
# PYTHONPATH=/app) quanto com ``python src/main.py`` (arquivo direto, sem
# contexto de pacote). ANTES de qualquer ``from src....``.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Bootstrap TLS -----------------------------------------------------------
# Rede corporativa faz inspeção TLS: sem o trust store do SO, o ``requests``
# falha no handshake com CERTIFICATE_VERIFY_FAILED. ``truststore`` usa o store
# do SO (CA raiz interna). Best-effort — inócuo no Cloud Run, necessário local.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from src.bq.loader import carregar_status_webcrawler
from src.config import settings
from src.fattureweb.session import TokenSession
from src.pipeline.instalacoes import listar_instalacoes
from src.pipeline.tabela import montar_tabela
from src.pipeline.webcrawler import enriquecer_com_data_fim


def _reportar_falhas(rotulo: str, falhas: list) -> None:
    if falhas:
        print(f"[!] {rotulo}: {len(falhas)} página(s) falharam (reprocessar só estas):")
        for page, skip, motivo in sorted(falhas):
            print(f"    página {page} (skip={skip}): {motivo}")


def main() -> None:
    # 1. Auth (single-thread, antes de qualquer pool -> evita re-login concorrente).
    ts = TokenSession()
    ts.login()

    # 2. Coleta das instalações da carteira (via clientes).
    instalacoes, falhas_inst = listar_instalacoes(
        ts, settings.CLIENTE_IDS, settings.PAGE_SIZE, settings.MAX_WORKERS
    )
    _reportar_falhas("instalações", falhas_inst)

    # 3. Modelagem (tabela base de 6 colunas).
    df = montar_tabela(instalacoes)

    # 4. Enriquecimento: maior data_fim do webcrawler (só status != sucesso),
    #    em blocos de WEBCRAWLER_CHUNK_SIZE ids por chamada (teto da URL).
    df, avisos_wc = enriquecer_com_data_fim(
        ts,
        df,
        page_size=settings.PAGE_SIZE,
        max_workers=settings.MAX_WORKERS,
        status_sucesso_id=settings.STATUS_SUCESSO_ID,
        chunk_size=settings.WEBCRAWLER_CHUNK_SIZE,
    )
    if avisos_wc:
        print(f"[!] webcrawler: {len(avisos_wc)} bloco(s) com aviso/erro:")
        for aviso in avisos_wc:
            print(f"    {aviso}")

    print(f"Linhas na tabela final: {len(df)}")

    # 5. Carga create-or-replace no BigQuery.
    destino = carregar_status_webcrawler(df)
    print(f"[OK] Carregado em {destino} (create-or-replace).")


if __name__ == "__main__":
    main()
