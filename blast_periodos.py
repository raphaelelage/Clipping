# -*- coding: utf-8 -*-
"""Quais periodos entram nas colunas de Net Adds do Blast.

A planilha do Blast tem UM driver — a celula `Input last month` — e dela derivam
todas as colunas. Este modulo e essa derivacao, em codigo, para que o app possa
mostrar o padrao ja preenchido e o dono sobrescrever quando quiser.

Regra do dono (01/10/2026), por posicao do mes dentro do trimestre:

    1o mes   mes · QTD · trimestre ANTERIOR · acumulado
    2o mes   mes · QTD · trimestre anterior · acumulado
    3o mes   mes · QTD · trimestre ATUAL (fechado) · acumulado

E o acumulado (4a coluna) nao e sempre YTD:

    dezembro        -> o ano inteiro que acabou de fechar (2025)
    1o trimestre    -> o ANO ANTERIOR completo (em jan-mar o YTD e curto demais
                       para dizer alguma coisa)
    demais meses    -> YTD

Conferido contra o Blast de jul-26 (1o mes de 3Q26), cujas colunas sao
`Jul-26 | QTD | 2Q26 | YTD` — exatamente o que `padrao(2026, 7)` devolve.
"""
from __future__ import annotations

# Em ingles: o Blast vai para cliente internacional e a tabela e colada como
# imagem, entao o rotulo tem que sair pronto (dono, 01/10/2026).
MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def rotulo_mes(ano: int, mes: int) -> str:
    """`jul-26`."""
    return f"{MESES[mes - 1]}-{ano % 100:02d}"


def rotulo_tri(ano: int, tri: int) -> str:
    """`2Q26`."""
    return f"{tri}Q{ano % 100:02d}"


def _tri_anterior(ano: int, tri: int) -> tuple[int, int]:
    return (ano - 1, 4) if tri == 1 else (ano, tri - 1)


def meses_do(periodo: dict) -> list[tuple[int, int]]:
    """Lista de (ano, mes) que o periodo soma — e o que o calculo de net adds usa."""
    t, a = periodo["tipo"], periodo["ano"]
    if t == "mes":
        return [(a, periodo["mes"])]
    if t == "trimestre":
        base = (periodo["tri"] - 1) * 3
        return [(a, base + i) for i in (1, 2, 3)]
    if t == "qtd":                       # do inicio do trimestre ate o mes
        base = (periodo["tri"] - 1) * 3
        return [(a, m) for m in range(base + 1, periodo["mes"] + 1)]
    if t == "ytd":
        return [(a, m) for m in range(1, periodo["mes"] + 1)]
    if t == "ano":
        return [(a, m) for m in range(1, 13)]
    raise ValueError(f"tipo de periodo desconhecido: {t!r}")


def _mes(ano, mes):
    return {"tipo": "mes", "ano": ano, "mes": mes, "rotulo": rotulo_mes(ano, mes)}


def _tri(ano, tri):
    return {"tipo": "trimestre", "ano": ano, "tri": tri, "rotulo": rotulo_tri(ano, tri)}


def _qtd(ano, mes):
    return {"tipo": "qtd", "ano": ano, "tri": (mes - 1) // 3 + 1, "mes": mes,
            "rotulo": "QTD"}


def _ytd(ano, mes):
    return {"tipo": "ytd", "ano": ano, "mes": mes, "rotulo": "YTD"}


def _ano(ano):
    return {"tipo": "ano", "ano": ano, "rotulo": str(ano)}


def padrao(ano: int, mes: int) -> list[dict]:
    """As 4 colunas de Net Adds para o mes de referencia, pela regra do dono."""
    tri = (mes - 1) // 3 + 1
    posicao = (mes - 1) % 3 + 1          # 1, 2 ou 3 dentro do trimestre

    if posicao == 3:
        coluna_tri = _tri(ano, tri)                    # trimestre que fechou
    else:
        ta, tt = _tri_anterior(ano, tri)
        coluna_tri = _tri(ta, tt)                      # ainda nao fechou: o anterior

    if mes == 12:
        acumulado = _ano(ano)                          # o ano inteiro fechou
    elif tri == 1:
        acumulado = _ano(ano - 1)                      # YTD curto demais
    else:
        acumulado = _ytd(ano, mes)

    return [_mes(ano, mes), _qtd(ano, mes), coluna_tri, acumulado]


def opcoes(ano: int, mes: int) -> list[dict]:
    """Tudo que o app pode oferecer nos dropdowns daquele mes, sem repetir."""
    tri = (mes - 1) // 3 + 1
    ta, tt = _tri_anterior(ano, tri)
    brutas = [_mes(ano, mes), _qtd(ano, mes), _tri(ano, tri), _tri(ta, tt),
              _ytd(ano, mes), _ano(ano), _ano(ano - 1)]
    vistos, out = set(), []
    for p in brutas:
        chave = (p["tipo"], p["ano"], p.get("mes"), p.get("tri"))
        if chave not in vistos:
            vistos.add(chave)
            out.append(p)
    return out


def cabecalho(periodo: dict) -> str:
    """O texto que vai no cabecalho da coluna no e-mail."""
    return periodo["rotulo"]


if __name__ == "__main__":
    for a, m in [(2026, 1), (2026, 2), (2026, 3), (2026, 7), (2026, 8),
                 (2026, 9), (2026, 12)]:
        cols = " | ".join(cabecalho(p) for p in padrao(a, m))
        print(f"{rotulo_mes(a, m)}  ->  {cols}")
