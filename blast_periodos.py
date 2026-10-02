# -*- coding: utf-8 -*-
"""Quais periodos entram nas colunas de Net Adds do Blast.

A configuracao guarda CHAVES RELATIVAS — "Último Mês", "Último Trimestre" — e nao
rotulos concretos como "Jul-26" ou "2Q26". O motivo e que a mesma configuracao
vale todo ano: se janeiro guardasse "Jan-26", em 2027 ela nao casaria com nada e a
coluna sumiria em silencio (foi o que aconteceu ao trocar os meses para ingles,
em 01/10/2026). O rotulo concreto so aparece no cabecalho do e-mail.

Regra do dono, por posicao do mes dentro do trimestre:

    1o e 2o mes   mes · QTD · trimestre ANTERIOR · acumulado
    3o mes        mes · QTD · trimestre que ACABOU de fechar · acumulado

E o acumulado nao e sempre YTD:

    dezembro       -> o ano que fechou
    1o trimestre   -> o ano ANTERIOR completo (em jan-mar o YTD e curto demais)
    demais meses   -> YTD
"""
from __future__ import annotations

MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

MES = "Último Mês"
QTD = "QTD"
TRI = "Último Trimestre"
YTD = "YTD"
ANO = "Último Ano"
CHAVES = [MES, QTD, TRI, YTD, ANO]


def rotulo_mes(ano: int, mes: int) -> str:
    return f"{MESES[mes - 1]}-{ano % 100:02d}"


def rotulo_tri(ano: int, tri: int) -> str:
    return f"{tri}Q{ano % 100:02d}"


def _tri_anterior(ano: int, tri: int) -> tuple[int, int]:
    return (ano - 1, 4) if tri == 1 else (ano, tri - 1)


def meses_do(periodo: dict) -> list[tuple[int, int]]:
    """Lista de (ano, mes) que o periodo soma — e o que o net adds usa."""
    t, a = periodo["tipo"], periodo["ano"]
    if t == "mes":
        return [(a, periodo["mes"])]
    if t == "trimestre":
        base = (periodo["tri"] - 1) * 3
        return [(a, base + i) for i in (1, 2, 3)]
    if t == "qtd":
        base = (periodo["tri"] - 1) * 3
        return [(a, m) for m in range(base + 1, periodo["mes"] + 1)]
    if t == "ytd":
        return [(a, m) for m in range(1, periodo["mes"] + 1)]
    if t == "ano":
        return [(a, m) for m in range(1, 13)]
    raise ValueError(f"tipo de periodo desconhecido: {t!r}")


def resolver(chave: str, ano: int, mes: int) -> dict | None:
    """Chave relativa -> periodo concreto, com o rotulo que vai no cabecalho."""
    tri = (mes - 1) // 3 + 1
    posicao = (mes - 1) % 3 + 1

    if chave == MES:
        return {"tipo": "mes", "ano": ano, "mes": mes,
                "rotulo": rotulo_mes(ano, mes)}
    if chave == QTD:
        return {"tipo": "qtd", "ano": ano, "tri": tri, "mes": mes, "rotulo": "QTD"}
    if chave == TRI:
        # no 3o mes o trimestre ACABOU de fechar; antes disso, o ultimo fechado
        # e o anterior
        ta, tt = (ano, tri) if posicao == 3 else _tri_anterior(ano, tri)
        return {"tipo": "trimestre", "ano": ta, "tri": tt,
                "rotulo": rotulo_tri(ta, tt)}
    if chave == YTD:
        return {"tipo": "ytd", "ano": ano, "mes": mes, "rotulo": "YTD"}
    if chave == ANO:
        # em dezembro o ano corrente fechou; nos demais meses, o ultimo ano
        # completo e o anterior
        a = ano if mes == 12 else ano - 1
        return {"tipo": "ano", "ano": a, "rotulo": str(a)}
    return None


def _mes(ano: int, mes: int) -> dict:
    """O periodo de UM mes — usado pelo MoM da tabela."""
    return resolver(MES, ano, mes)


def padrao_chaves(mes: int) -> list[str]:
    """As chaves que cada mes usa por padrao."""
    tri = (mes - 1) // 3 + 1
    acumulado = ANO if (mes == 12 or tri == 1) else YTD
    return [MES, QTD, TRI, acumulado]


def padrao(ano: int, mes: int) -> list[dict]:
    """As colunas concretas do mes, pela regra."""
    return [resolver(c, ano, mes) for c in padrao_chaves(mes)]


def das_chaves(chaves, ano: int, mes: int) -> list[dict]:
    """Converte a configuracao do app em colunas concretas."""
    out = [resolver(c, ano, mes) for c in (chaves or [])]
    return [c for c in out if c]


def cabecalho(periodo: dict) -> str:
    return periodo["rotulo"]


if __name__ == "__main__":
    for a, m in [(2026, 1), (2026, 3), (2026, 7), (2026, 9), (2026, 12)]:
        print(f"{rotulo_mes(a, m)}  ->  "
              + " | ".join(cabecalho(p) for p in padrao(a, m)))
