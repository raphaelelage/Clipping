# -*- coding: utf-8 -*-
"""Monta as tabelas do Blast (Lives · Net Adds · Base Growth) a partir do historico.

Entrada: um DataFrame longo com uma linha por (registro, mes) e a quantidade de
beneficiarios — que e o formato em que o historico fica no BigQuery
(`sala_situacao_blast`). Saida: uma tabela por secao (medico, odonto, corporate),
na ordem e na hierarquia que `blast_grupos.json` define.

O que a tabela faz, e so isso:

    Lives        = beneficiarios no mes de referencia, em milhares
    Net Adds     = variacao SOMADA nos meses do periodo (ver blast_periodos)
    Base Growth  = essa variacao dividida pela base do inicio do periodo

Net adds de um mes M = beneficiarios(M) - beneficiarios(M-1). Para um periodo de
varios meses, a soma dos net adds telescopa: e base(fim) - base(antes do inicio).
E assim que o Blast calcula, e e o que mantem QTD/YTD consistentes com a soma dos
meses individuais.

Regra dura: NUNCA inventar numero. Mes sem dado na base vira `None` e a celula sai
vazia no e-mail — nao zero, que seria lido como "ficou estavel".
"""
from __future__ import annotations

import io
import json
import os

import blast_periodos as bp

AQUI = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(AQUI, "blast_grupos.json")


def carregar_grupos(caminho: str = CFG) -> dict:
    return json.load(io.open(caminho, encoding="utf-8"))


def _mes_antes(ano: int, mes: int) -> tuple[int, int]:
    return (ano - 1, 12) if mes == 1 else (ano, mes - 1)


class Serie:
    """Beneficiarios por (registro, ano, mes), com soma por conjunto de registros."""

    def __init__(self, df, col_registro="registro", col_ano="ano", col_mes="mes",
                 col_valor="beneficiarios"):
        self.d = {}
        for reg, a, m, v in zip(df[col_registro], df[col_ano], df[col_mes],
                                df[col_valor]):
            if v is None:
                continue
            self.d[(str(reg).zfill(6), int(a), int(m))] = float(v)

    def base(self, registros, ano: int, mes: int):
        """Soma dos registros naquele mes. None se NENHUM registro tem dado —
        o mes nao foi coletado. Registro ausente com outros presentes conta 0:
        e operadora que saiu da base (Bio Saude em 2026), nao falta de dado."""
        achou, total = False, 0.0
        for r in registros:
            v = self.d.get((str(r).zfill(6), ano, mes))
            if v is not None:
                achou = True
                total += v
        return total if achou else None

    def net_adds(self, registros, periodo: dict):
        """base(ultimo mes do periodo) - base(mes anterior ao primeiro)."""
        meses = bp.meses_do(periodo)
        if not meses:
            return None
        fim = self.base(registros, *meses[-1])
        ini = self.base(registros, *_mes_antes(*meses[0]))
        if fim is None or ini is None:
            return None
        return fim - ini

    def crescimento(self, registros, periodo: dict):
        """Net adds do periodo sobre a base do inicio. None se a base for 0."""
        meses = bp.meses_do(periodo)
        if not meses:
            return None
        ini = self.base(registros, *_mes_antes(*meses[0]))
        na = self.net_adds(registros, periodo)
        if ini in (None, 0) or na is None:
            return None
        return na / ini


def _registros_do(secao_cfg: dict, grupo: str, item_registro: str) -> list[str]:
    """Os registros que uma linha soma.

    - sub-linha com registro proprio -> so ele
    - sub-linha "Others" -> os registros do grupo que NAO aparecem nas outras
      sub-linhas (e o residual, igual ao da planilha)
    - linha de grupo -> todos os registros do grupo
    """
    if item_registro:
        return [item_registro]
    return secao_cfg["por_grupo"].get(grupo, [])


def montar(serie: Serie, secao: str, ano: int, mes: int, colunas: list[dict] | None,
           grupos: dict | None = None, serie_mercado: "Serie | None" = None) -> dict:
    """Uma tabela pronta para virar HTML/Excel.

    `colunas` = as 4 de Net Adds (de `blast_periodos.padrao` ou do override do app).
    Base Growth sai MoM (o mes) e YoY (12 meses), como no print.
    """
    grupos = grupos or carregar_grupos()
    # corporate_medico usa os grupos do medico; corporate_odonto, os do odonto
    chave = ("odonto" if secao.endswith("odonto") else "medico")         if secao.startswith("corporate") else secao
    cfg = grupos[chave]
    colunas = colunas or bp.padrao(ano, mes)

    mom = bp._mes(ano, mes)
    yoy = {"tipo": "ytd", "ano": ano, "mes": mes, "rotulo": "YoY"}   # 12 meses atras

    def _yoy(regs):
        atual = serie.base(regs, ano, mes)
        antes = serie.base(regs, ano - 1, mes)
        if atual is None or antes in (None, 0):
            return None
        return atual / antes - 1

    linhas = []
    todos_grupos = [b["grupo"] for b in cfg["layout"]
                    if b["grupo"] not in ("Market", "Others")]

    for bloco in cfg["layout"]:
        g = bloco["grupo"]
        regs = None if g in ("Market", "Others") else _registros_do(cfg, g, "")

        def _linha(rotulo, registros, nivel):
            if registros is None:
                return {"rotulo": rotulo, "nivel": nivel, "lives": None,
                        "registros": [], "net_adds": [None] * len(colunas),
                        "mom": None, "yoy": None, "pendente": g}
            lives = serie.base(registros, ano, mes)
            return {
                "rotulo": rotulo, "nivel": nivel, "registros": list(registros),
                "lives": None if lives is None else lives / 1000.0,
                "net_adds": [(None if (v := serie.net_adds(registros, c)) is None
                              else v / 1000.0) for c in colunas],
                "mom": serie.crescimento(registros, mom),
                "yoy": _yoy(registros),
            }

        if g == "Market":
            linhas.append(_mercado(serie_mercado, colunas, ano, mes, bloco["rotulo"]))
            continue
        if g == "Others":
            linhas.append(_residual(linhas, serie_mercado, colunas, ano, mes,
                                    bloco["rotulo"]))
            continue
        linhas.append(_linha(bloco["rotulo"], regs, 0))
        usados = [i["registro"] for i in bloco["itens"] if i["registro"]]
        for item in bloco["itens"]:
            if item["registro"]:
                sub = [item["registro"]]
            elif item["rotulo"] in cfg.get("sub_grupo", {}):
                # sub-linha que e, ela propria, um grupo (HAPV do odonto abre em
                # Hapvida e NDI, cada um com seus registros)
                sub = cfg["por_grupo"].get(cfg["sub_grupo"][item["rotulo"]], [])
            else:                                   # "Others" do grupo = residual
                sub = [r for r in cfg["por_grupo"].get(g, []) if r not in usados]
            linhas.append(_linha(item["rotulo"], sub, 1))

    return {"secao": secao, "ano": ano, "mes": mes,
            "periodos": colunas,
            "colunas": [bp.cabecalho(c) for c in colunas],
            "mes_rotulo": bp.rotulo_mes(ano, mes), "linhas": linhas}


# --------------------------------------------------------------------------- #
# Market e Others
#
# "Market" NAO e a soma das operadoras do de-para: e a linha agregada que a
# propria ANS publica, e inclui todas as operadoras do pais. "Others" e o que
# sobra — Market menos os grupos nomeados. Calcular Others por diferenca (e nao
# somando "todo o resto") e o que faz a coluna fechar com o total.
# --------------------------------------------------------------------------- #
_VAZIA = {"lives": None, "mom": None, "yoy": None}


def _mercado(sm, colunas, ano, mes, rotulo):
    if sm is None:
        return {"rotulo": rotulo, "nivel": 0, "registros": [],
                "net_adds": [None] * len(colunas), **_VAZIA}
    reg = ["MERCADO"]
    lives = sm.base(reg, ano, mes)
    atual, antes = lives, sm.base(reg, ano - 1, mes)
    return {
        "rotulo": rotulo, "nivel": 0, "registros": ["MERCADO"],
        "lives": None if lives is None else lives / 1000.0,
        "net_adds": [(None if (v := sm.net_adds(reg, c)) is None else v / 1000.0)
                     for c in colunas],
        "mom": sm.crescimento(reg, bp._mes(ano, mes)),
        "yoy": (None if atual is None or antes in (None, 0) else atual / antes - 1),
    }


def _residual(linhas, sm, colunas, ano, mes, rotulo):
    """Market menos a soma dos grupos de topo ja montados."""
    mkt = _mercado(sm, colunas, ano, mes, "Market")
    if mkt["lives"] is None:
        return {"rotulo": rotulo, "nivel": 0, "registros": [],
                "net_adds": [None] * len(colunas), **_VAZIA}
    topos = [l for l in linhas if l["nivel"] == 0 and l["lives"] is not None]

    def menos(campo, i=None):
        base = mkt[campo][i] if i is not None else mkt[campo]
        if base is None:
            return None
        for l in topos:
            v = l[campo][i] if i is not None else l[campo]
            if v is None:
                return None
            base -= v
        return base

    lives = menos("lives")
    na = [menos("net_adds", i) for i in range(len(colunas))]
    # crescimento do residual: net adds do mes sobre a base do mes anterior
    ant_mkt = sm.base(["MERCADO"], *_mes_antes(ano, mes))
    ant_topos = None
    if ant_mkt is not None:
        ant_topos = ant_mkt
        for l in topos:
            r = l.get("_regs")
            ant_topos = None if r is None else ant_topos
    mom = (None if (lives is None or na[0] is None or not lives or lives == na[0])
           else na[0] / (lives - na[0]))
    return {"rotulo": rotulo, "nivel": 0, "lives": lives, "net_adds": na,
            "registros": [], "residual": True, "mom": mom, "yoy": None}
