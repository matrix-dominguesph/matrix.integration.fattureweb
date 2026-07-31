"""Testes offline da coleta nível fatura — sem rede e sem ``.env``.

    python -m testes.test_unitario
"""

from __future__ import annotations

from src.config import Settings

from src.pipeline.tabela import CAMPOS_INSTALACAO, montar_tabela
from src.pipeline.webcrawler import enriquecer_com_data_fim
from src.pipeline.faturas import (
    COLUNAS_SAIDA,
    TENTATIVAS_REPESCA,
    ColetaFaturasIncompleta,
    _conferir_corte,
    coletar_faturas,
    combinar,
    montar_tabela_faturas,
)
from testes import Checador
from testes.falso_fattureweb import FattureWebFalso, registro

c = Checador("Testes unitários — pipeline.faturas (offline)")

# ---------------------------------------------------------------------------------------
c.secao("ingestão de instalações: coluna instalacao_ativa")

inst = montar_tabela([
    {"id": 1, "cliente_apelido": "MATRIX FÁCIL B - COOPERATIVA", "distribuidora_sigla": "ceal",
     "status_webcrawlers_id": 10, "erro_processamento": "",
     "descricao_status_webcrawler": "ok", "status": True},
    {"id": 2, "cliente_apelido": "MATRIX FÁCIL B - ASSOCIAÇÃO", "distribuidora_sigla": "cemigd",
     "status_webcrawlers_id": 4, "erro_processamento": "",
     "descricao_status_webcrawler": "credencial", "status": False},
    # registro sem a chave `status` (a API pode omitir): não deve estourar
    {"id": 3, "cliente_apelido": "x", "distribuidora_sigla": "ems",
     "status_webcrawlers_id": 6, "erro_processamento": "", "descricao_status_webcrawler": "y"},
])
c.checa("instalacao_ativa existe na tabela", "instalacao_ativa" in inst.columns,
        str(list(inst.columns)))
c.checa("vem do campo `status` do /instalacoes",
        inst["instalacao_ativa"].tolist()[:2] == [True, False])
c.checa("registro sem a chave `status` vira None (não estoura)",
        inst["instalacao_ativa"].isna().iloc[2])
c.checa("`status` está na projeção pedida à API", "status" in CAMPOS_INSTALACAO,
        str(CAMPOS_INSTALACAO))
c.checa("instalacao_ativa é o nome de saída, não `status` (evita confundir com o do crawler)",
        "status" not in inst.columns)


# ---------------------------------------------------------------------------------------
c.secao("enriquecimento consulta TODAS as instalações, não só as em erro")


class _WebcrawlerFalso:
    """Dublê de /webcrawlers/execucoes: registra quais ids foram consultados."""

    def __init__(self):
        self.base_url = "https://falso.test"
        self.consultados: set = set()

    def request(self, method, path, **kwargs):
        params = kwargs.get("params") or {}
        ids = [int(x) for x in str(params.get("instalacao_id", "")).split(",") if x]
        self.consultados.update(ids)
        dados = [{"instalacao_id": i, "data_fim": f"2026-07-{(i % 28) + 1:02d}T10:00:00-03:00"}
                 for i in ids]

        class R:
            @staticmethod
            def json():
                return {"status": "sucesso", "dados": dados}

        return R()


falso = _WebcrawlerFalso()
enriquecido, avisos_wc = enriquecer_com_data_fim(falso, inst, page_size=180, max_workers=2,
                                                chunk_size=20)
c.checa("consultou a UC em SUCESSO também (antes ela era pulada)",
        1 in falso.consultados, f"consultados={sorted(falso.consultados)}")
c.checa("consultou as 3 UCs", falso.consultados == {1, 2, 3}, str(sorted(falso.consultados)))
c.checa("data_fim preenchido para todas, inclusive a de sucesso",
        enriquecido["data_fim"].notna().all(), str(enriquecido["data_fim"].tolist()))
c.checa("sem avisos", avisos_wc == [], str(avisos_wc))

# ---------------------------------------------------------------------------------------
c.secao("montar_tabela_faturas")

df = montar_tabela_faturas([
    registro(1, instalacao=10, dia=1, origem='SITE', nome='X'),
    registro(2, instalacao=11, dia=2, conteudo_nulo=True),
    registro(3, instalacao=12, dia=3, dia_atualizacao=9, origem='EMAIL', distribuidora='ems'),
])
c.checa("colunas na ordem esperada", list(df.columns) == COLUNAS_SAIDA, str(list(df.columns)))
c.checa("id_fatura vem de dados[i].id", df['id_fatura'].tolist() == [1, 2, 3])
c.checa("conteudo=None não estoura e vira None",
        bool(df.loc[1, ['origem', 'cliente_instalacao', 'data_emissao']].isna().all()))
c.checa("data_criacao e data_atualizacao são colunas distintas",
        df.loc[2, 'data_atualizacao'] > df.loc[2, 'data_criacao'],
        "fatura reprocessada: atualizacao > criacao")
c.checa("fatura não reprocessada: atualizacao == criacao",
        df.loc[0, 'data_atualizacao'] == df.loc[0, 'data_criacao'])
c.checa("origem é passthrough (EMAIL preservado, sem enum no código)",
        df.loc[2, 'origem'] == 'EMAIL')
c.checa("lista vazia mantém o schema",
        list(montar_tabela_faturas([]).columns) == COLUNAS_SAIDA)

# ---------------------------------------------------------------------------------------
c.secao("_conferir_corte (guarda-corpo do filtro ignorado)")

avisos = _conferir_corte(
    [{'data_atualizacao': '2026-01-01T00:00:00-03:00'}], '2026-06-01T00:00:00-03:00'
)
c.checa("avisa quando volta registro anterior ao corte",
        any('IGNORADO' in a for a in avisos), str(avisos)[:80])
c.checa("avisa quando há offsets de fuso diferentes",
        any('offsets diferentes' in a for a in _conferir_corte(
            [{'data_criacao': '2026-06-02T00:00:00-03:00'},
             {'data_criacao': '2026-06-03T00:00:00+00:00'}], '2026-06-01')))
c.checa("corte respeitado -> nenhum aviso",
        _conferir_corte([{'data_atualizacao': '2026-07-01T00:00:00-03:00',
                          'data_criacao': '2026-07-01T00:00:00-03:00'}], '2026-06-01') == [])

# ---------------------------------------------------------------------------------------
c.secao("coletar_faturas — paginação")

TODOS = [registro(100 + i, instalacao=1000 + i, dia=(i % 28) + 1) for i in range(95)]

api = FattureWebFalso(TODOS)
regs, avisos = coletar_faturas(api, [1], page_size=10, max_workers=4)
c.checa("coleta tudo com limit pequeno (10 páginas)", len(regs) == 95, f"{len(regs)} de 95")
c.checa("sem avisos", avisos == [], str(avisos)[:120])
c.checa("ids únicos", len({r['id'] for r in regs}) == 95)
c.checa("limit grande dá o mesmo conjunto",
        {r['id'] for r in coletar_faturas(FattureWebFalso(TODOS), [1], page_size=180)[0]}
        == {r['id'] for r in regs})

# Total múltiplo exato do limit: é o caso que levava um passo de paginação a mais (404).
regs_e, av_e = coletar_faturas(FattureWebFalso(TODOS[:90]), [1], page_size=10, max_workers=4)
c.checa("total múltiplo exato do limit não quebra nem perde linha",
        len(regs_e) == 90 and av_e == [], f"{len(regs_e)} linhas, avisos={av_e}")

# ---------------------------------------------------------------------------------------
c.secao("coletar_faturas — conjunto vazio (404 da API) é caso normal")

api_vazio = FattureWebFalso([])
regs_v, av_v = coletar_faturas(api_vazio, [1], page_size=10, max_workers=4)
c.checa("zero registros", regs_v == [])
c.checa("zero avisos (404 não é falha)", av_v == [], str(av_v)[:120])
c.checa("nenhuma página pedida quando total=0",
        all('skip' not in ch for ch in api_vazio.chamadas),
        f"{len(api_vazio.chamadas)} chamada(s), só o count")
c.checa("sem clientes -> nem chega a chamar a API",
        coletar_faturas(FattureWebFalso(TODOS), [], page_size=10) == ([], []))

# ---------------------------------------------------------------------------------------
c.secao("coletar_faturas — corte incremental sobre data_atualizacao")

# Uma fatura antiga (dia 2) reprocessada hoje (dia 25): o corte por atualização tem de
# pegá-la, e um corte por criação não pegaria.
REPROCESSADA = registro(999, instalacao=7777, dia=2, dia_atualizacao=25, origem='API')
BASE = [registro(200 + i, instalacao=2000 + i, dia=(i % 20) + 1) for i in range(30)]
CORTE = '2026-07-20T00:00:00.000000-03:00'

api_corte = FattureWebFalso(BASE + [REPROCESSADA])
regs_c, av_c = coletar_faturas(api_corte, [1], page_size=10, atualizadas_desde=CORTE)
ids_c = {r['id'] for r in regs_c}
c.checa("fatura REPROCESSADA (criada antes do corte) volta",
        999 in ids_c, f"{len(ids_c)} registro(s) no lote")
c.checa("faturas antigas e não reprocessadas NÃO voltam",
        all(r['data_atualizacao'] >= CORTE for r in regs_c))
c.checa("o filtro enviado é data_atualizacao_inicio",
        all(ch.get('data_atualizacao_inicio') == CORTE for ch in api_corte.chamadas))
c.checa("sem avisos quando o corte é respeitado", av_c == [], str(av_c)[:120])

_, av_i = coletar_faturas(
    FattureWebFalso(BASE, ignora_filtro=True), [1], page_size=180, atualizadas_desde=CORTE
)
c.checa("filtro ignorado pela API é DETECTADO",
        any('IGNORADO' in a for a in av_i), str(av_i)[:110])

# ---------------------------------------------------------------------------------------
c.secao(f"coletar_faturas — falha de página: repesca até {TENTATIVAS_REPESCA}x ou aborta")

c.checa("configurado para até 5 tentativas", TENTATIVAS_REPESCA >= 5, str(TENTATIVAS_REPESCA))

regs_t, av_t = coletar_faturas(
    FattureWebFalso(TODOS, falhas_por_skip={30: 1}), [1], page_size=10, max_workers=4
)
c.checa("1 falha transitória: repesca e fica COMPLETO", len(regs_t) == 95, f"{len(regs_t)}/95")
c.checa("1 falha transitória: não sobra aviso", av_t == [], str(av_t)[:120])

# 4 falhas na mesma página: ainda dentro das 5 tentativas.
regs_4, av_4 = coletar_faturas(
    FattureWebFalso(TODOS, falhas_por_skip={30: 4}), [1], page_size=10, max_workers=4
)
c.checa("4 falhas seguidas na mesma página: ainda recupera",
        len(regs_4) == 95 and av_4 == [], f"{len(regs_4)}/95, avisos={av_4}")

regs_d, av_d = coletar_faturas(
    FattureWebFalso(TODOS, falhas_por_skip={10: 1, 50: 2}), [1], page_size=10, max_workers=4
)
c.checa("duas páginas falhando: ambas recuperadas",
        len(regs_d) == 95 and av_d == [], f"{len(regs_d)}/95, avisos={av_d}")

try:
    coletar_faturas(
        FattureWebFalso(TODOS, falhas_por_skip={40: 99}), [1], page_size=10, max_workers=4
    )
    c.checa("falha permanente: ABORTA (não grava parcial)", False, "não levantou")
except ColetaFaturasIncompleta as e:
    c.checa("falha permanente: ABORTA (não grava parcial)", True, str(e)[:70])

# Resultado encurtou entre o count e as páginas: a última página vira 404. Isso NÃO é
# falha de transporte — não deve ser repescado 5x nem abortar o job.
api_encurtou = FattureWebFalso(TODOS, total_extra=15)
try:
    regs_e2, av_e2 = coletar_faturas(api_encurtou, [1], page_size=10, max_workers=4)
    c.checa("página que virou 404 (resultado encurtou) NÃO aborta", True,
            f"{len(regs_e2)} linha(s) coletadas")
    c.checa("404 não vira aviso de falha", av_e2 == [], str(av_e2)[:120])
    c.checa("traz tudo o que de fato existe", len(regs_e2) == 95, f"{len(regs_e2)} de 95")
except ColetaFaturasIncompleta as e:
    c.checa("página que virou 404 (resultado encurtou) NÃO aborta", False, str(e)[:90])

# ---------------------------------------------------------------------------------------
c.secao("combinar — upsert por id_fatura")

gravado = montar_tabela_faturas([
    registro(1, instalacao=10, dia=1, origem='SITE'),
    registro(2, instalacao=11, dia=2, origem='SITE'),
])
# id=2 volta reprocessada (origem mudou), id=3 é nova.
coletado = montar_tabela_faturas([
    registro(2, instalacao=11, dia=2, dia_atualizacao=25, origem='WEBCRAWLER'),
    registro(3, instalacao=12, dia=3, origem='EMAIL'),
])
final = combinar(gravado, coletado)
c.checa("tabela final tem 3 linhas (1 mantida, 1 substituída, 1 nova)", len(final) == 3,
        f"{len(final)} linhas")
c.checa("id_fatura segue único", bool(final['id_fatura'].is_unique))
c.checa("a versão da API VENCE a gravada (origem atualizada)",
        final.loc[final['id_fatura'] == 2, 'origem'].iloc[0] == 'WEBCRAWLER')
c.checa("a linha não tocada foi preservada",
        final.loc[final['id_fatura'] == 1, 'origem'].iloc[0] == 'SITE')
c.checa("colunas preservadas e ordenadas", list(final.columns) == COLUNAS_SAIDA)
c.checa("ordenado por data_criacao desc",
        final['data_criacao'].tolist() == sorted(final['data_criacao'], reverse=True))

c.checa("sem tabela gravada -> vira o próprio lote",
        len(combinar(None, coletado)) == 2)
c.checa("lote vazio -> tabela gravada intacta",
        set(combinar(gravado, coletado.iloc[0:0])['id_fatura']) == {1, 2})
c.checa("gravado vazio e lote vazio -> vazio com schema",
        list(combinar(gravado.iloc[0:0], coletado.iloc[0:0]).columns) == COLUNAS_SAIDA)

# ---------------------------------------------------------------------------------------
c.secao("ciclo completo: snapshot -> corte -> upsert (sem BigQuery)")

MUNDO = [registro(300 + i, instalacao=3000 + i, dia=(i % 25) + 1) for i in range(40)]
snapshot = combinar(None, montar_tabela_faturas(coletar_faturas(
    FattureWebFalso(MUNDO), [1], page_size=15)[0]))
c.checa("snapshot pega tudo", len(snapshot) == 40, f"{len(snapshot)} de 40")

# O mundo muda: uma fatura antiga é reprocessada e uma nova entra.
alvo = int(snapshot['id_fatura'].iloc[-1])  # a mais antiga
MUNDO_2 = [
    registro(alvo, instalacao=9999, dia=1, dia_atualizacao=28, origem='WEBCRAWLER')
    if r['id'] == alvo else r
    for r in MUNDO
] + [registro(9001, instalacao=4000, dia=29, origem='EMAIL')]

corte = str(snapshot['data_criacao'].max())
lote = montar_tabela_faturas(coletar_faturas(
    FattureWebFalso(MUNDO_2), [1], page_size=15, atualizadas_desde=corte)[0])
c.checa("o lote incremental é pequeno", len(lote) < len(snapshot), f"{len(lote)} linha(s)")
c.checa("o lote traz a fatura NOVA", 9001 in set(lote['id_fatura']))
c.checa("o lote traz a fatura REPROCESSADA (o furo que o data_criacao deixava)",
        alvo in set(lote['id_fatura']))

final2 = combinar(snapshot, lote)
c.checa("tabela final tem 41 linhas", len(final2) == 41, f"{len(final2)}")
c.checa("id_fatura único", bool(final2['id_fatura'].is_unique))
c.checa("a reprocessada foi ATUALIZADA, não duplicada",
        final2.loc[final2['id_fatura'] == alvo, 'origem'].iloc[0] == 'WEBCRAWLER'
        and (final2['id_fatura'] == alvo).sum() == 1)

# Idempotência: rodar de novo sem o mundo mudar não altera nada.
corte2 = str(final2['data_criacao'].max())
lote2 = montar_tabela_faturas(coletar_faturas(
    FattureWebFalso(MUNDO_2), [1], page_size=15, atualizadas_desde=corte2)[0])
final3 = combinar(final2, lote2)
c.checa("2ª execução seguida não muda a tabela",
        len(final3) == len(final2) and set(final3['id_fatura']) == set(final2['id_fatura']),
        f"{len(final3)} vs {len(final2)}")


# ---------------------------------------------------------------------------------------
c.secao("o .env não depende do diretório de onde se roda")

from pathlib import Path as _Path  # noqa: E402

_env = Settings.model_config.get("env_file")
c.checa("env_file é caminho ABSOLUTO", _Path(str(_env)).is_absolute(), str(_env))
c.checa("aponta para a raiz deste repo",
        _Path(str(_env)).parent == _Path(__file__).resolve().parent.parent,
        str(_Path(str(_env)).parent))
c.checa("não é o './.env' relativo (quebrava ao rodar de outra pasta)",
        str(_env) not in (".env", "./.env"), str(_env))

c.encerrar()
