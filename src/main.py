"""Orquestração da ingestão do Monitor de Aquisição de Fatura (Cloud Run Job).

Duas tabelas no mesmo projeto/dataset, com regimes diferentes:
  - ``BQ_TABLE`` (status por instalação) — **snapshot completo** a cada execução. É o
    retrato de agora, não tem histórico a preservar.
  - ``BQ_TABLE_FATURAS`` (nível fatura) — **acumulativa, com update incremental**: lê a
    tabela gravada, usa o ``MAX(data_criacao)`` dela como corte de
    ``data_atualizacao_inicio`` na ``/faturas`` (pega fatura nova E reprocessada numa
    passada), faz **upsert** por ``id_fatura`` em pandas e reescreve a tabela. É essa
    etapa que justifica o job rodar agendado.

Fluxo fim-a-fim (7 etapas):
  1. auth               — TokenSession.login()
  2. coleta             — listar_instalacoes (carteira -> clientes -> instalações)
  3. modelagem          — montar_tabela (6 colunas)
  4. enriquecimento     — maior data_fim do webcrawler p/ status != sucesso
  5. carga              — create-or-replace no BigQuery
  6. faturas            — tabela gravada -> corte -> /faturas por cliente_id -> upsert
  7. carga das faturas  — create-or-replace da tabela combinada

Rodar (local ou container, a partir da raiz do repo):
    python -m src.main

Reprocessar a tabela de faturas do zero: ``FATURAS_FULL_REFRESH=true``.
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

from src.bq.loader import carregar_faturas, carregar_status_webcrawler
from src.bq.reader import corte_incremental, ler_faturas
from src.config import settings
from src.fattureweb.session import TokenSession
from src.pipeline.faturas import coletar_faturas, combinar, montar_tabela_faturas
from src.pipeline.instalacoes import listar_instalacoes
from src.pipeline.tabela import montar_tabela
from src.pipeline.webcrawler import enriquecer_com_data_fim


def _reportar_falhas(rotulo: str, falhas: list) -> None:
    if falhas:
        print(f"[!] {rotulo}: {len(falhas)} página(s) falharam (reprocessar só estas):")
        for page, skip, motivo in sorted(falhas):
            print(f"    página {page} (skip={skip}): {motivo}")


def _reportar_avisos(rotulo: str, avisos: list) -> None:
    if avisos:
        print(f"[!] {rotulo}: {len(avisos)} bloco(s) com aviso/erro:")
        for aviso in avisos:
            print(f"    {aviso}")


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
    _reportar_avisos("webcrawler", avisos_wc)

    print(f"Linhas na tabela final: {len(df)}")

    # 5. Carga create-or-replace no BigQuery (esta tabela é sempre snapshot completo).
    destino = carregar_status_webcrawler(df)
    print(f"[OK] Carregado em {destino} (create-or-replace).")

    # 6. Nível fatura, no mesmo escopo de clientes da etapa 2. A tabela gravada decide o
    #    modo: sem tabela (ou vazia) -> snapshot completo; com tabela -> incremental a
    #    partir do MAX(data_criacao) dela. Depois da etapa 5 de propósito: se /faturas
    #    falhar, a tabela de status já está carregada.
    if settings.FATURAS_FULL_REFRESH:
        print("Faturas: FATURAS_FULL_REFRESH=true -> snapshot completo forçado.")
        gravado = None
    else:
        gravado = ler_faturas()

    registros_faturas, avisos_faturas = coletar_faturas(
        ts,
        settings.CLIENTE_IDS,
        page_size=settings.PAGE_SIZE,
        max_workers=settings.MAX_WORKERS,
        atualizadas_desde=corte_incremental(gravado),
    )
    _reportar_avisos("faturas", avisos_faturas)

    coletado = montar_tabela_faturas(registros_faturas)
    if coletado.empty and gravado is not None:
        print("[OK] Nenhuma fatura nova ou reprocessada desde o corte — nada a gravar.")
        return

    # 7. Upsert por id_fatura (a versão da API vence) e carga create-or-replace.
    df_faturas = combinar(gravado, coletado)
    print(f"Linhas na tabela de faturas: {len(df_faturas)}")
    destino_faturas = carregar_faturas(df_faturas)
    print(f"[OK] Carregado em {destino_faturas} (create-or-replace).")


if __name__ == "__main__":
    main()
