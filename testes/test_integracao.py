"""Testes de integração contra a API real do Fattureweb — precisa de ``.env``.

Não toca no BigQuery: o estado da tabela é simulado recortando o próprio snapshot, então a
suíte pode rodar sem credencial de GCP e sem escrever em lugar nenhum.

    python -m testes.test_integracao
"""

from __future__ import annotations

# --- Bootstrap TLS (igual ao main): rede corporativa inspeciona TLS ---------------------
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from src.config import settings
from src.fattureweb.session import TokenSession
from src.pipeline.faturas import (
    COLUNAS_SAIDA,
    coletar_faturas,
    combinar,
    montar_tabela_faturas,
)
from src.pipeline.instalacoes import listar_instalacoes
from testes import Checador

c = Checador("Testes de integração — /faturas contra a API real")

if not settings.FATTUREWEB_BASE_URL:
    print("\n[ABORTADO] FATTUREWEB_BASE_URL vazio — falta o .env. Nada foi testado.")
    raise SystemExit(2)

ts = TokenSession()
ts.login()
print(f"login OK em {ts.base_url}")

# ---------------------------------------------------------------------------------------
c.secao("snapshot completo")

registros, avisos = coletar_faturas(
    ts, settings.CLIENTE_IDS,
    page_size=settings.PAGE_SIZE, max_workers=settings.MAX_WORKERS,
)
snapshot = montar_tabela_faturas(registros)
c.checa("trouxe linhas", len(snapshot) > 0, f"{len(snapshot)} faturas")
c.checa("sem avisos", avisos == [], str(avisos)[:150])
c.checa("id_fatura único", bool(snapshot['id_fatura'].is_unique))
c.checa("schema completo", list(snapshot.columns) == COLUNAS_SAIDA)
c.checa("nenhuma coluna 100% nula", not snapshot.isna().all().any(),
        str(snapshot.isna().sum().to_dict()))
c.checa("data_atualizacao >= data_criacao em TODAS as linhas",
        bool((snapshot['data_atualizacao'] >= snapshot['data_criacao']).all()),
        "premissa do corte por atualização")

reprocessadas = snapshot['data_atualizacao'] > snapshot['data_criacao']
print(f"  faturas reprocessadas na base: {int(reprocessadas.sum())} de {len(snapshot)}")
print(f"  origens: {snapshot['origem'].value_counts(dropna=False).to_dict()}")

# ---------------------------------------------------------------------------------------
c.secao("offset de fuso único (premissa do MAX lexicográfico)")

for coluna in ('data_criacao', 'data_atualizacao'):
    offsets = set(snapshot[coluna].dropna().astype(str).str[-6:])
    c.checa(f"{coluna} com um só offset", len(offsets) == 1, str(offsets))
    lexico = snapshot[coluna].dropna().astype(str).max()
    import pandas as pd  # noqa: E402  (local: só para a conferência abaixo)
    crono = pd.to_datetime(snapshot[coluna].dropna(), format='ISO8601').max().isoformat()
    c.checa(f"MAX lexicográfico de {coluna} == MAX cronológico", lexico == crono,
            f"{lexico} vs {crono}")

# ---------------------------------------------------------------------------------------
c.secao("update incremental: corte = MAX(data_criacao), filtro em data_atualizacao")

# Simula a tabela gravada: tudo, menos as linhas mais recentes.
limite = sorted(snapshot['data_criacao'].unique())[-4]
gravado = snapshot[snapshot['data_criacao'] <= limite]
corte = str(gravado['data_criacao'].max())
print(f"  BQ simulado: {len(gravado)} linha(s) | corte={corte}")

lote_reg, lote_avisos = coletar_faturas(
    ts, settings.CLIENTE_IDS,
    page_size=settings.PAGE_SIZE, max_workers=settings.MAX_WORKERS,
    atualizadas_desde=corte,
)
lote = montar_tabela_faturas(lote_reg)
c.checa("lote é MUITO menor que o snapshot", len(lote) < len(snapshot),
        f"{len(lote)} vs {len(snapshot)}")
c.checa("sem avisos (filtro reconhecido, offset único)", lote_avisos == [],
        str(lote_avisos)[:150])
c.checa("toda linha do lote tem data_atualizacao >= corte",
        bool((lote['data_atualizacao'] >= corte).all()))
c.checa("o corte é inclusivo: a linha do próprio corte volta",
        bool(lote['data_criacao'].isin([corte]).any() or (lote['data_atualizacao'] == corte).any()))

# O filtro por atualização tem de ser SUPERCONJUNTO do filtro por criação.
so_criacao = set(snapshot.loc[snapshot['data_criacao'] >= corte, 'id_fatura'])
c.checa("pega tudo que um corte por data_criacao pegaria (e mais)",
        so_criacao <= set(lote['id_fatura']),
        f"por criação {len(so_criacao)} ⊆ por atualização {len(lote)}")

# ---------------------------------------------------------------------------------------
c.secao("upsert reconstrói a tabela")

final = combinar(gravado, lote)
c.checa("id_fatura único depois do upsert", bool(final['id_fatura'].is_unique))
c.checa("nenhuma linha perdida", set(snapshot['id_fatura']) <= set(final['id_fatura']),
        f"{len(final)} linhas na tabela final vs {len(snapshot)} no snapshot")
c.checa("nenhuma linha inventada", set(final['id_fatura']) <= set(snapshot['id_fatura']),
        "se falhar, entrou fatura nova durante o teste — não é bug")

# ---------------------------------------------------------------------------------------
c.secao("idempotência")

corte2 = str(final['data_criacao'].max())
lote2 = montar_tabela_faturas(coletar_faturas(
    ts, settings.CLIENTE_IDS,
    page_size=settings.PAGE_SIZE, max_workers=settings.MAX_WORKERS,
    atualizadas_desde=corte2,
)[0])
final2 = combinar(final, lote2)
c.checa("2ª execução seguida não altera o conjunto",
        set(final2['id_fatura']) == set(final['id_fatura']),
        f"{len(final2)} vs {len(final)}")

# ---------------------------------------------------------------------------------------
c.secao("conjunto vazio e paginação")

vazio, av_vazio = coletar_faturas(
    ts, settings.CLIENTE_IDS, page_size=settings.PAGE_SIZE,
    max_workers=settings.MAX_WORKERS, atualizadas_desde='2027-01-01',
)
c.checa("corte no futuro -> zero registros, zero avisos", vazio == [] and av_vazio == [],
        f"{len(vazio)} registro(s), avisos={av_vazio}")

pequeno, av_pequeno = coletar_faturas(
    ts, settings.CLIENTE_IDS, page_size=37, max_workers=settings.MAX_WORKERS,
)
c.checa("limit=37 dá o mesmo conjunto que limit padrão",
        {r['id'] for r in pequeno} == set(snapshot['id_fatura']),
        f"{len(pequeno)} vs {len(snapshot)}")
c.checa("sem avisos com limit pequeno", av_pequeno == [], str(av_pequeno)[:150])

# ---------------------------------------------------------------------------------------
c.secao("escopo: as faturas são da carteira MATRIX FÁCIL B")

instalacoes, falhas_inst = listar_instalacoes(
    ts, settings.CLIENTE_IDS, settings.PAGE_SIZE, settings.MAX_WORKERS
)
c.checa("listagem de instalações sem falha de página", falhas_inst == [],
        str(falhas_inst)[:150])
ids_carteira = {i.get('id') for i in instalacoes}
fora = set(snapshot['id_instalacao'].dropna().astype('int64')) - ids_carteira
c.checa("nenhuma fatura de instalação fora da carteira", not fora,
        f"{len(fora)} fora | {len(ids_carteira)} instalações na carteira")

c.encerrar()
