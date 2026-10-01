# -*- coding: utf-8 -*-
"""As tabelas do Blast em HTML (e-mail) e em Excel.

O HTML imita o print que o dono usa: numero em milhares sem casa decimal,
percentual com uma casa, e o heatmap vermelho-verde nas colunas de Net Adds e
Base Growth. Celula sem dado sai VAZIA, nunca zero.
"""
from __future__ import annotations

VERDE = (198, 239, 206)
VERMELHO = (255, 199, 206)
CINZA = "#4a4a4a"


def _cor(v, maxabs):
    """Fundo da celula: intensidade proporcional ao maior valor da coluna."""
    if v is None or not maxabs:
        return ""
    f = min(abs(v) / maxabs, 1.0) ** 0.6
    r, g, b = VERDE if v > 0 else VERMELHO
    r = int(255 + (r - 255) * f)
    g = int(255 + (g - 255) * f)
    b = int(255 + (b - 255) * f)
    return f"background:rgb({r},{g},{b});"


def _n(v):
    return "" if v is None else f"{v:,.0f}".replace(",", ".")


def _p(v):
    return "" if v is None else f"{v * 100:.1f}%".replace(".", ",")


def tabela_html(t: dict, titulo: str) -> str:
    cols = t["colunas"]
    linhas = t["linhas"]
    # escala por coluna, ignorando a linha de mercado (que e uma ordem maior)
    corpo = [l for l in linhas if l["rotulo"] != "Market"]
    esc_na = [max((abs(l["net_adds"][i]) for l in corpo
                   if l["net_adds"][i] is not None), default=0)
              for i in range(len(cols))]
    esc_mom = max((abs(l["mom"]) for l in corpo if l["mom"] is not None), default=0)
    esc_yoy = max((abs(l["yoy"]) for l in corpo if l["yoy"] is not None), default=0)

    th = ("padding:3px 7px;font-size:11px;font-weight:bold;color:#fff;"
          "background:#9e1b32;border:1px solid #fff;text-align:center;")
    td = "padding:2px 7px;font-size:11px;border:1px solid #e3e3e3;text-align:right;"

    out = [f'<table style="border-collapse:collapse;font-family:Arial,sans-serif;'
           f'margin:0 0 18px 0;">']
    out.append(f'<tr><th style="{th}text-align:left;">{titulo}</th>'
               f'<th style="{th}">{t["mes_rotulo"]}</th>'
               f'<th style="{th}" colspan="{len(cols)}">Net Adds</th>'
               f'<th style="{th}" colspan="2">Base Growth</th></tr>')
    out.append(f'<tr><th style="{th}"></th><th style="{th}">Lives</th>'
               + "".join(f'<th style="{th}">{c}</th>' for c in cols)
               + f'<th style="{th}">MoM</th><th style="{th}">YoY</th></tr>')

    for l in linhas:
        topo = l["nivel"] == 0
        peso = "font-weight:bold;" if topo else ""
        recuo = "" if topo else "padding-left:20px;"
        cor_txt = "" if topo else f"color:{CINZA};"
        out.append(f'<tr><td style="{td}text-align:left;{peso}{recuo}{cor_txt}">'
                   f'{l["rotulo"]}</td>')
        out.append(f'<td style="{td}{peso}">{_n(l["lives"])}</td>')
        for i, v in enumerate(l["net_adds"]):
            out.append(f'<td style="{td}{peso}{_cor(v, esc_na[i])}">{_n(v)}</td>')
        out.append(f'<td style="{td}{peso}{_cor(l["mom"], esc_mom)}">'
                   f'{_p(l["mom"])}</td>')
        out.append(f'<td style="{td}{peso}{_cor(l["yoy"], esc_yoy)}">'
                   f'{_p(l["yoy"])}</td></tr>')
    out.append("</table>")
    return "".join(out)


def email_html(tabelas: list[tuple[dict, str]], mes_rotulo: str,
               avisos: list[str] | None = None) -> str:
    corpo = "".join(tabela_html(t, titulo) for t, titulo in tabelas)
    av = ""
    if avisos:
        itens = "".join(f"<li>{a}</li>" for a in avisos)
        av = (f'<ul style="color:#9e1b32;font-size:12px;font-family:Arial;'
              f'padding-left:18px;">{itens}</ul>')
    return (f'<div style="font-family:Arial,sans-serif;color:#222;">'
            f'<h2 style="font-size:17px;margin:0 0 2px;">ANS — Net Adds '
            f'({mes_rotulo})</h2>'
            f'<p style="color:#888;font-size:12px;margin:0 0 14px;">'
            f'Sala de Situação da ANS · planilha completa em anexo</p>'
            f'{av}{corpo}</div>')


def excel(caminho: str, historicos: dict, tabelas: list[tuple[dict, str]] | None = None):
    """Uma aba por base (as 3 que hoje saem em arquivos separados)."""
    import pandas as pd
    with pd.ExcelWriter(caminho, engine="openpyxl") as w:
        for aba, df in historicos.items():
            df.to_excel(w, sheet_name=aba[:31], index=False)
        for t, titulo in (tabelas or []):
            linhas = [{"": l["rotulo"], "Lives": l["lives"],
                       **{c: l["net_adds"][i] for i, c in enumerate(t["colunas"])},
                       "MoM": l["mom"], "YoY": l["yoy"]} for l in t["linhas"]]
            pd.DataFrame(linhas).to_excel(w, sheet_name=f"Tab {titulo}"[:31],
                                          index=False)
    return caminho
