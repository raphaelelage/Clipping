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


def coletar(registros=None, workers: int = 4, log=print) -> pd.DataFrame:
    """Historico longo de todas as operadoras do config + o mercado."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    sc = _sc()
    registros = sorted(registros or registros_do_config())
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

    partes, feitos = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_uma, t) for t in tarefas]):
            d = fut.result()
            feitos += 1
            if len(d):
                partes.append(d)
            if feitos % 20 == 0:
                log(f"[coleta] {feitos}/{len(tarefas)} series")

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
