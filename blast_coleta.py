# -*- coding: utf-8 -*-
"""Puxa da Sala de Situacao o historico que o Blast precisa.

Duas queries do Pentaho, as mesmas que o notebook antigo usava — a diferenca e
que aqui a sessao se autentica sozinha, com as credenciais PUBLICAS embutidas no
embed do painel, em vez de cookies colados do navegador (que expiram em horas e
impedem rodar no GitHub):

    ComposicaoCarteira.cda / qSerieTempo   serie mensal de UMA operadora,
                                           por tipo de contratacao
                                           params: codOperadora + Segmento
    Perfil do Setor.cda    / qgrafbenef    serie mensal do MERCADO

`Segmento` aceita ASSIST (assistencia medica), ODONTO ou TODOS.

So busca as operadoras que estao em `blast_grupos.json` — sao ~40, nao os 4.175
do painel inteiro. O resto do mercado entra pela linha agregada.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

import pandas as pd

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE_CONSOLIDADA = (r"C:\Users\Raphael\OneDrive\Documentos\1. Profissional"
                    r"\VS Code\Codigos\Base Consolidada")
CDA_CARTEIRA = "/public/Sala Externo/ComposicaoCarteira.cda"
CDA_PERFIL = "/public/Sala Externo/Perfil do Setor.cda"
MES_NUM = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
           "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def _sc():
    """O coletor da Sala. Usa o da Base Consolidada quando existe (PC do dono);
    senao cai no embutido abaixo — no runner do GitHub aquela pasta nao existe,
    e era por isso que o fallback quebrava."""
    if os.path.isdir(BASE_CONSOLIDADA):
        if BASE_CONSOLIDADA not in sys.path:
            sys.path.insert(0, BASE_CONSOLIDADA)
        try:
            from alarmes import sala_situacao_coleta
            return sala_situacao_coleta
        except Exception:                                         # noqa: BLE001
            pass
    return _embutido


class _embutido:
    """Copia minima de sala_situacao_coleta: sessao + doQuery, sem cookies.

    A autenticacao sai das credenciais PUBLICAS do embed do painel; o GET na
    landing semeia os cookies do WAF (F5). Retry so em erro de REDE — erro HTTP
    e deterministico (501 = parametro faltando) e repetir so gasta tempo."""

    LANDING = ("https://www.ans.gov.br/images/stories/Materiais_para_pesquisa/"
               "Perfil_setor/sala-de-situacao.html")
    PENTAHO = "https://www.ans.gov.br/pentaho"
    CRED = {"userid": "penanoprod", "password": "PRDAUpent001"}
    HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/148.0.0.0 Safari/537.36",
            "Accept": "*/*", "Origin": "https://www.ans.gov.br",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.ans.gov.br/pentaho/"}

    @staticmethod
    def sessao():
        import requests
        import urllib3
        urllib3.disable_warnings()
        s = requests.Session()
        s.headers.update(_embutido.HDRS)
        s.get(_embutido.LANDING, timeout=60, verify=False)
        return s

    @staticmethod
    def coletar(s, cda, query, params=None):
        import time
        import requests
        data = {"path": cda, "dataAccessId": query, "outputIndexId": "1",
                "pageSize": "0", "pageStart": "0"}
        data.update({(k if k.startswith("param") else f"param{k}"):
                     ("" if v is None else str(v)) for k, v in (params or {}).items()})
        url = f"{_embutido.PENTAHO}/plugin/cda/api/doQuery"
        ultimo = None
        for i in range(3):
            try:
                r = s.post(url, params=_embutido.CRED, data=data, timeout=45,
                           verify=False)
            except requests.exceptions.RequestException as exc:
                ultimo = exc
                time.sleep(1.5 * (i + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} em doQuery/{query}")
            j = r.json()
            cols = [c["colName"] for c in j.get("metadata", [])]
            return pd.DataFrame(j.get("resultset", []), columns=cols or None)
        raise RuntimeError(f"doQuery/{query} falhou na rede: {ultimo}")


def registros_do_config(caminho: str | None = None) -> set[str]:
    cfg = json.load(io.open(caminho or os.path.join(AQUI, "blast_grupos.json"),
                            encoding="utf-8"))
    out = set()
    for secao in ("medico", "odonto", "corporate"):
        for regs in cfg[secao]["por_grupo"].values():
            out.update(str(r).zfill(6) for r in regs)
    return out


def _competencia(txt: str):
    # os dois formatos que a Sala devolve: JUL/2026 (operadora) e jul/26 (mercado)
    m = re.match(r"\s*([A-Za-zÇç]{3})[/\-](\d{2,4})\s*$", str(txt))
    if not m:
        return None
    mes = MES_NUM.get(m.group(1)[:3].lower())
    if not mes:
        return None
    ano = int(m.group(2))
    return (2000 + ano if ano < 100 else ano), mes


def _longo(df: pd.DataFrame, registro: str, secao: str) -> pd.DataFrame:
    """As 3 colunas do CDA (competencia, contratacao, qtd) no formato do BQ."""
    if df.empty or len(df.columns) < 3:
        return pd.DataFrame()
    comp, seg, qtd = df.columns[0], df.columns[1], df.columns[2]
    c = df[comp].map(_competencia)
    out = pd.DataFrame({
        "registro": registro,
        "ano": [x[0] if x else None for x in c],
        "mes": [x[1] if x else None for x in c],
        "segmento": df[seg].astype(str).str.strip(),
        "beneficiarios": pd.to_numeric(df[qtd], errors="coerce"),
        "secao": secao,
    })
    return out.dropna(subset=["ano", "mes", "beneficiarios"])


def todas_operadoras(s_sess=None) -> list[str]:
    """Os registros do dropdown do painel, lidos AO VIVO a cada rodada.

    Nunca cacheado de proposito: operadora nova entra no dropdown sem avisar, e
    uma lista congelada a deixaria de fora para sempre (dono, 02/10/2026). Custa
    uma requisicao."""
    sc = _sc()
    s_sess = s_sess or sc.sessao()
    d = sc.coletar(s_sess, CDA_CARTEIRA, "qOperadoras")
    col = d.columns[0]
    return sorted({str(v).strip().zfill(6) for v in d[col] if str(v).strip()})


def novas_operadoras(atuais, log=print) -> list[str]:
    """Quais registros do dropdown ainda nao existem no historico do BigQuery.

    So informativo — a varredura ja pega todas de qualquer jeito. Serve para o
    log dizer que apareceu gente nova, em vez de a mudanca passar despercebida."""
    try:
        import blast_bq
        c = blast_bq.cliente()
        ja = {str(r["registro"]).zfill(6) for r in
              c.query(f"SELECT DISTINCT registro FROM `{blast_bq.FQN}`").result()}
    except Exception as exc:                                      # noqa: BLE001
        log(f"[operadoras] não consegui comparar com o BigQuery ({str(exc)[:60]})")
        return []
    novas = sorted(set(atuais) - ja)
    if novas:
        log(f"[operadoras] {len(novas)} nova(s) desde a última rodada: "
            f"{', '.join(novas[:8])}{'…' if len(novas) > 8 else ''}")
    return novas


def coletar(registros=None, workers: int = 8, log=print, todas: bool = False) -> pd.DataFrame:
    """Historico longo das operadoras pedidas + o mercado.

    `todas=True` varre o dropdown inteiro (~3.700 operadoras, ~30 min). Medido em
    02/10/2026: 0,24 s por serie, e o paralelismo SATURA entre 4 e 10 robos — com
    20 ou 32 o tempo PIORA (0,28 e 0,33 s/serie), porque o WAF serializa. Entao
    mais threads nao e o caminho; 8 e o ponto de equilibrio."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    sc = _sc()
    if registros is None:
        if todas:
            registros = todas_operadoras()
            log(f"[operadoras] dropdown tem {len(registros)} — lido agora, "
                f"para pegar operadora nova")
            novas_operadoras(registros, log=log)
        else:
            registros = sorted(registros_do_config())
    registros = sorted(registros)
    local = threading.local()

    def sessao():
        if not hasattr(local, "s"):
            local.s = sc.sessao()
        return local.s

    tarefas = [(r, seg, secao) for r in registros
               for seg, secao in (("ASSIST", "medico"), ("ODONTO", "odonto"))]

    def _uma(t):
        reg, seg, secao = t
        try:
            d = sc.coletar(sessao(), CDA_CARTEIRA, "qSerieTempo",
                           {"codOperadora": reg, "Segmento": seg})
            return _longo(d, reg, secao)
        except Exception as exc:                                  # noqa: BLE001
            log(f"[coleta] {reg}/{seg} falhou: {str(exc)[:80]}")
            return pd.DataFrame()

    # Progresso com percentual e ETA: numa varredura completa sao ~7.400 series e
    # meia hora de janela aberta — sem estimativa, quem olha nao sabe se travou.
    import time as _t
    partes, feitos, t0 = [], 0, _t.time()
    passo = max(1, len(tarefas) // 40)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_uma, t) for t in tarefas]):
            d = fut.result()
            feitos += 1
            if len(d):
                partes.append(d)
            if feitos % passo == 0 or feitos == len(tarefas):
                dec = _t.time() - t0
                falta = dec / feitos * (len(tarefas) - feitos)
                log(f"[coleta] {feitos:>5}/{len(tarefas)} "
                    f"({100 * feitos // len(tarefas):>3}%) "
                    f"· {dec / 60:.1f} min · faltam ~{falta / 60:.0f} min")

    for seg, rotulo in (("ASSIST", "Assistência Médica"),
                        ("ODONTO", "Exclusivamente Odontológico")):
        try:
            d = sc.coletar(sessao(), CDA_PERFIL, "qgrafbenef",
                           {"Segmento": seg, "UF": "TODOS", "Modalidade": "TODOS"})
            m = _longo(d, "MERCADO", "mercado")
            if len(m):
                m["segmento"] = rotulo
                partes.append(m)
        except Exception as exc:                                  # noqa: BLE001
            log(f"[coleta] mercado/{seg} falhou: {str(exc)[:80]}")

    if not partes:
        raise RuntimeError("Sala de Situação: nenhuma série coletada")
    out = pd.concat(partes, ignore_index=True)
    out["ano"] = out["ano"].astype(int)
    out["mes"] = out["mes"].astype(int)
    log(f"[coleta] {len(out):,} linhas | até "
        f"{out['ano'].max()}-{out[out['ano'] == out['ano'].max()]['mes'].max():02d}")
    return out


# --------------------------------------------------------------------------- #
# Quebras adicionais (fase 2)
#
# IMPORTANTE: elas NAO cruzam entre si nem com o tempo. Cada query devolve uma
# marginal do MES CORRENTE — faixa etaria por sexo, ou UF, e so. Nao existe
# "registro x mes x faixa x UF" na API; quem quiser isso precisa do Caderno 2.0.
# Por isso entram como linhas com `dimensao` propria, lado a lado, e NUNCA devem
# ser somadas junto com a contratacao (contariam a mesma vida varias vezes).
# --------------------------------------------------------------------------- #
QUEBRAS = [("qGraficoFaixaEtaria", "Faixa etária"), ("qMapa", "UF")]


def _longo_quebra(df, registro, secao, dimensao, ano, mes):
    if df.empty or len(df.columns) < 2:
        return pd.DataFrame()
    linhas = []
    if dimensao == "Faixa etária":      # IDADE | MASCULINO | FEMININO
        for _, r in df.iterrows():
            faixa = str(r.iloc[0]).strip()
            for sexo, col in (("M", 1), ("F", 2)):
                if col < len(df.columns):
                    v = pd.to_numeric(r.iloc[col], errors="coerce")
                    if pd.notna(v):
                        linhas.append((f"{faixa} ({sexo})", abs(float(v))))
    else:                                # ESTADO | QUANTIDADE
        for _, r in df.iterrows():
            v = pd.to_numeric(r.iloc[1], errors="coerce")
            if pd.notna(v):
                linhas.append((str(r.iloc[0]).strip(), float(v)))
    if not linhas:
        return pd.DataFrame()
    return pd.DataFrame({
        "registro": registro, "ano": ano, "mes": mes,
        "segmento": [a for a, _ in linhas],
        "beneficiarios": [b for _, b in linhas],
        "secao": secao, "dimensao": dimensao})


def coletar_quebras(registros, ano: int, mes: int, workers: int = 8,
                    log=print) -> pd.DataFrame:
    """Faixa etaria e UF do mes corrente, por operadora."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    sc = _sc()
    local = threading.local()
    tarefas = [(r, seg, secao, q, dim)
               for r in sorted(registros)
               for seg, secao in (("ASSIST", "medico"), ("ODONTO", "odonto"))
               for q, dim in QUEBRAS]

    def _uma(t):
        reg, seg, secao, q, dim = t
        if not hasattr(local, "s"):
            local.s = sc.sessao()
        try:
            d = sc.coletar(local.s, CDA_CARTEIRA, q,
                           {"codOperadora": reg, "Segmento": seg})
            return _longo_quebra(d, reg, secao, dim, ano, mes)
        except Exception:                                         # noqa: BLE001
            return pd.DataFrame()

    partes, feitos = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_uma, t) for t in tarefas]):
            d = fut.result()
            feitos += 1
            if len(d):
                partes.append(d)
            if feitos % max(1, len(tarefas) // 10) == 0:
                log(f"[quebras] {feitos}/{len(tarefas)} "
                    f"({100 * feitos // len(tarefas)}%)")
    if not partes:
        return pd.DataFrame()
    out = pd.concat(partes, ignore_index=True)
    log(f"[quebras] {len(out):,} linhas ({out['dimensao'].nunique()} dimensões)")
    return out


# --------------------------------------------------------------------------- #
# Mercado como pseudo-operadora (registro 0)
#
# A soma das operadoras NAO chega ao mercado: faltam ~6% no medico e ~2,7% no
# odonto, de forma estavel em todos os meses. Nao e falha da coleta — operadora
# a operadora o numero bate exato com o painel; e o proprio agregado da ANS que
# e maior que a soma das series que ele mesmo publica. Como nao da para somar
# ate o mercado, ele entra como UMA LINHA, com registro 0, e as mesmas quebras
# que uma operadora tem (dono, 02/10/2026).
#
# ATENCAO: por isso a aba Dados NAO deve ser somada inteira — o registro 0 ja e
# o total. Filtre registro<>0 para somar operadoras.
# --------------------------------------------------------------------------- #
# O parametro da query e a SIGLA, mas o qMapa (quebra por operadora) devolve o
# NOME por extenso. Sem este de-para a aba UF fica com 57 valores — "AC" e "Acre"
# lado a lado, que nao somam juntos (visto na varredura de 02/10/2026).
UF_NOME = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
    "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
    "MG": "Minas Gerais", "MS": "Mato Grosso do Sul", "MT": "Mato Grosso",
    "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí",
    "PR": "Paraná", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RO": "Rondônia", "RR": "Roraima", "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo",
    "TO": "Tocantins",
    # dois pseudo-estados do painel; sem eles a soma das UFs fica 0,09% abaixo
    "EX": "Exterior", "XX": "Não identificado"}
UFS = list(UF_NOME)
REG_MERCADO = "000000"


def coletar_mercado(workers: int = 8, log=print) -> pd.DataFrame:
    """O mercado como operadora: serie total + serie por UF, ambas mensais."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    sc = _sc()
    local = threading.local()

    def _ses():
        if not hasattr(local, "s"):
            local.s = sc.sessao()
        return local.s

    tarefas = [(seg, secao, uf)
               for seg, secao in (("ASSIST", "medico"), ("ODONTO", "odonto"))
               for uf in ["TODOS"] + UFS]

    def _uma(t):
        seg, secao, uf = t
        try:
            d = sc.coletar(_ses(), CDA_PERFIL, "qgrafbenef",
                           {"Segmento": seg, "UF": uf, "Modalidade": "TODOS"})
            out = _longo(d, REG_MERCADO, secao)
            if not len(out):
                return pd.DataFrame()
            if uf == "TODOS":
                out["segmento"] = "Total do setor"
                out["dimensao"] = "Contratação"
            else:
                out["segmento"] = UF_NOME.get(uf, uf)
                out["dimensao"] = "UF"
            return out
        except Exception:                                         # noqa: BLE001
            return pd.DataFrame()

    partes = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_uma, t) for t in tarefas]):
            d = fut.result()
            if len(d):
                partes.append(d)
    if not partes:
        return pd.DataFrame()
    out = pd.concat(partes, ignore_index=True)
    log(f"[mercado] {len(out):,} linhas (total + {out[out.dimensao=='UF'].segmento.nunique()} UFs)")
    return out
