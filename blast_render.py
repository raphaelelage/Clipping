# -*- coding: utf-8 -*-
"""As tabelas do Blast em HTML (e-mail) e em Excel.

As tabelas sao coladas como IMAGEM no WhatsApp, as duas num print so. Dai todas
as decisoes de forma: larguras fixas e iguais entre as tabelas, linhas finas,
cor forte (a compressao do WhatsApp lava o tom), fundo branco e uma coluna
branca de respiro em cada lado — para o recorte nao encostar no numero.

CADA elemento declara `background` E `color`, sem excecao e sem herdar. O e-mail
do dono abre com FUNDO PRETO (04/10/2026): o que so herda o branco aparece preto
no print, e — pior — texto sem `color` proprio e invertido para claro pelo
cliente e some no fundo branco da celula. Declarar os dois e o que mantem a
tabela identica no claro e no escuro.
"""
from __future__ import annotations

import datetime as _dt
import re

import blast_periodos as bp

VERDE = (140, 214, 160)
VERMELHO = (247, 150, 162)
CINZA = "#4a4a4a"
TINTA = "#222"            # cor do texto, sempre explicita — ver nota abaixo
VINHO = "#9e1b32"
L_ROTULO = 168
L_NUM = 56
L_RESPIRO = 14          # coluna branca em cada lado


def _cor(v, maxabs):
    if v is None or not maxabs:
        return ""
    f = min(abs(v) / maxabs, 1.0) ** 0.42
    r, g, b = VERDE if v > 0 else VERMELHO
    return (f"background:rgb({int(255 + (r - 255) * f)},"
            f"{int(255 + (g - 255) * f)},{int(255 + (b - 255) * f)});")


def _n(v):
    """Milhar com virgula, decimal com ponto. Conta que nao fecha vira n.a."""
    return "n.a." if v is None else f"{v:,.0f}"


def _p(v):
    return "n.a." if v is None else f"{v * 100:.1f}%"


# Publicos para o blast_textos: o rascunho de WhatsApp vai junto com o print da
# tabela, entao os dois tem que formatar numero do MESMO jeito.
fmt_milhares = _n
fmt_pct = _p


def tabela_html(t: dict, titulo: str) -> str:
    cols = t["colunas"]
    linhas = t["linhas"]
    corpo = [l for l in linhas if l["rotulo"] != "Market"]
    esc_na = [max((abs(l["net_adds"][i]) for l in corpo
                   if l["net_adds"][i] is not None), default=0)
              for i in range(len(cols))]
    esc_mom = max((abs(l["mom"]) for l in corpo if l["mom"] is not None), default=0)
    esc_yoy = max((abs(l["yoy"]) for l in corpo if l["yoy"] is not None), default=0)

    th = ("padding:1px 5px;font-size:11px;font-weight:bold;color:#fff;"
          f"background:{VINHO};border:1px solid #fff;text-align:center;"
          "white-space:nowrap;line-height:1.15;")
    # `background:#fff` em TODA celula, nao so na tabela: celula que apenas
    # herda o fundo e a que o Gmail/Outlook inverte no modo escuro, e o print
    # sai com rotulo cinza no meio de numero branco. Quem tem cor sobrescreve,
    # porque o `_cor` entra depois no mesmo style (dono, 04/10/2026).
    td = ("padding:0 5px;font-size:11px;border:1px solid #e3e3e3;"
          "text-align:right;white-space:nowrap;line-height:1.3;"
          f"background:#fff;color:{TINTA};")
    br = f"background:#fff;border:0;width:{L_RESPIRO}px;"   # respiro lateral

    n_num = 1 + len(cols) + 2
    grupo = (f'<colgroup><col style="width:{L_RESPIRO}px">'
             f'<col style="width:{L_ROTULO}px">'
             + f'<col style="width:{L_NUM}px">' * n_num
             + f'<col style="width:{L_RESPIRO}px"></colgroup>')
    larg = L_ROTULO + L_NUM * n_num + 2 * L_RESPIRO
    o = [f'<table bgcolor="#ffffff" cellpadding="0" cellspacing="0" border="0" '
         f'style="border-collapse:collapse;font-family:Arial,sans-serif;'
         f'background:#fff;margin:0;table-layout:fixed;width:{larg}px;">{grupo}']

    o.append(f'<tr><td style="{br}"></td><th style="{th}text-align:left;">{titulo}</th>'
             f'<th style="{th}">{t["mes_rotulo"]}</th>'
             f'<th style="{th}" colspan="{len(cols)}">Net Adds</th>'
             f'<th style="{th}" colspan="2">Base Growth</th>'
             f'<td style="{br}"></td></tr>')
    o.append(f'<tr><td style="{br}"></td><th style="{th}"></th>'
             f'<th style="{th}">Lives</th>'
             + "".join(f'<th style="{th}">{c}</th>' for c in cols)
             + f'<th style="{th}">MoM</th><th style="{th}">YoY</th>'
             f'<td style="{br}"></td></tr>')

    for l in linhas:
        topo = l["nivel"] == 0
        peso = "font-weight:bold;" if topo else ""
        recuo = "" if topo else "padding-left:20px;"
        cor_txt = "" if topo else f"color:{CINZA};"
        o.append(f'<tr><td style="{br}"></td>'
                 f'<td style="{td}text-align:left;{peso}{recuo}{cor_txt}">'
                 f'{l["rotulo"]}</td>')
        o.append(f'<td style="{td}{peso}">{_n(l["lives"])}</td>')
        for i, v in enumerate(l["net_adds"]):
            o.append(f'<td style="{td}{peso}{_cor(v, esc_na[i])}">{_n(v)}</td>')
        o.append(f'<td style="{td}{peso}{_cor(l["mom"], esc_mom)}">{_p(l["mom"])}</td>')
        o.append(f'<td style="{td}{peso}{_cor(l["yoy"], esc_yoy)}">{_p(l["yoy"])}</td>'
                 f'<td style="{br}"></td></tr>')
    o.append("</table>")
    return "".join(o)


def email_html(tabelas, mes_rotulo: str, avisos=None, texto: str = "") -> str:
    """Tabelas empilhadas, coladas, com faixa branca entre elas e nas pontas —
    para o print sair com margem sem precisar de edicao."""
    faixa = ('<div style="height:12px;background:#fff;line-height:12px;'
             'font-size:12px;">&nbsp;</div>')
    corpo = faixa + faixa.join(tabela_html(t, ti) for t, ti in tabelas) + faixa
    av = ""
    if avisos:
        itens = "".join(f'<li style="background:#fff;color:{VINHO};">{a}</li>'
                        for a in avisos)
        av = (f'<ul style="color:{VINHO};background:#fff;font-size:12px;'
              f'font-family:Arial;padding:6px 6px 6px 22px;margin:0 0 8px;">'
              f'{itens}</ul>')
    txt = ""
    if texto:
        txt = (f'<pre style="font-family:Arial,sans-serif;font-size:13px;'
               f'white-space:pre-wrap;background:#f6f6f6;color:{TINTA};'
               f'padding:10px;margin:10px 0 0;'
               f'border-left:3px solid {VINHO};">{texto}</pre>')
    # Envelope em TABELA, nao em div: numa div a tabela mais larga que a tela
    # transborda e a parte de fora cai no preto da caixa de entrada (visto em
    # 04/10/2026 ao simular o e-mail escuro). A celula de uma tabela cresce com
    # o conteudo, entao o branco acompanha. `color-scheme: light only` pede ao
    # cliente que nao inverta; quem ignora ja encontra cor e fundo em tudo.
    dentro = (f'<div style="font-family:Arial,sans-serif;color:{TINTA};'
              f'background:#fff;color-scheme:light only;">'
              f'<h2 style="font-size:17px;margin:0 0 2px;color:{TINTA};'
              f'background:#fff;">ANS — Net Adds ({mes_rotulo})</h2>'
              f'<p style="color:#777;background:#fff;font-size:12px;'
              f'margin:0 0 10px;">'
              f'Sala de Situação da ANS · planilha completa em anexo</p>'
              f'{av}{corpo}{txt}</div>')
    return (f'<table bgcolor="#ffffff" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%" style="background:#fff;'
            f'border-collapse:collapse;color-scheme:light only;">'
            f'<tr><td bgcolor="#ffffff" style="background:#fff;padding:12px;">'
            f'{dentro}</td></tr></table>')


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
ROTULO_SECAO = {"medico": "Médico-hospitalar", "odonto": "Odontológico",
                "mercado": "Mercado Total",
                "corporate_medico": "Médico-hospitalar",
                "corporate_odonto": "Odontológico"}
# Uma aba por dimensao. O nome da coluna da quebra muda com ela — "segmento" nao
# dizia nada (dono, 02/10/2026). A primeira aba e a de contratacao.
ABAS = {"Contratação": ("Tipo de contratação", ["tipo de contratação"]),
        "Faixa etária": ("Faixa etária", ["sexo", "idade"]),
        "UF": ("UF", ["UF"])}
ABA_DADOS = "Tipo de contratação"
_RX_FAIXA = re.compile(r"^\s*(.+?)\s*\(([MF])\)\s*$")


def _dados_longos(df, dimensao: str):
    """Uma aba de dados: data · secao · registro · <quebra> · beneficiarios.

    A data vem PRIMEIRO (dono, 02/10/2026) e e o 1o dia do mes, data de verdade —
    mmm-yy e so exibicao. `registro` e numero, entao 000043 vira 43; o 0 e o
    mercado. A linha agregada antiga (secao "mercado") nao entra: ela virou o
    registro 0 dentro de medico/odonto.
    """
    import pandas as pd
    d = df[df["secao"] != "mercado"].copy()
    if "dimensao" in d.columns:
        # SO a aba de contratacao aceita linha sem dimensao: o historico vem do
        # `coletar()`, que nao tem essa coluna, e ao concatenar vira NaN. As abas
        # de quebra exigem correspondencia exata — sem isso o NaN vazava para
        # todas e cada quebra saia com 1 milhao de linhas (visto em 02/10/2026).
        if dimensao == "Contratação":
            d = d[d["dimensao"].isna() | (d["dimensao"] == "Contratação")]
        else:
            d = d[d["dimensao"] == dimensao]
    d["secao"] = d["secao"].map(lambda x: ROTULO_SECAO.get(x, x))
    d["data"] = [_dt.date(int(a), int(m), 1) for a, m in zip(d["ano"], d["mes"])]
    d["registro"] = pd.to_numeric(d["registro"], errors="coerce")

    if dimensao == "Faixa etária":
        # "20 (M)" vira duas colunas: quem olha quer filtrar sexo sem mexer em texto
        sexo, idade = [], []
        for v in d["segmento"].astype(str):
            m = _RX_FAIXA.match(v)
            sexo.append(m.group(2) if m else "")
            idade.append(m.group(1) if m else v)
        d["sexo"], d["idade"] = sexo, idade
        cols = ["data", "secao", "registro", "sexo", "idade", "beneficiarios"]
    else:
        d[ABAS[dimensao][1][0]] = d["segmento"]
        cols = ["data", "secao", "registro", ABAS[dimensao][1][0], "beneficiarios"]
    return d[cols]


def _formula_sumifs(secao_rotulo, registros, periodo, n_dados, segmento=None):
    """Net adds de um periodo = base(fim) - base(antes do inicio), somando os
    registros do grupo. SUMPRODUCT envolve o SUMIFS para aceitar a lista."""
    meses = bp.meses_do(periodo)
    if not meses or not registros:
        return None
    fa, fm = meses[-1]
    ia, im = meses[0]
    ia, im = (ia - 1, 12) if im == 1 else (ia, im - 1)
    if registros == ["MERCADO"]:
        regs = "0"                      # o mercado e a pseudo-operadora 0
    else:
        regs = ";".join(str(int(r)) for r in registros if str(r).isdigit())
    if not regs:
        return None
    # A=data  B=secao  C=registro  D=quebra  E=beneficiarios
    aba = f"'{ABA_DADOS}'"
    lim = (f"$B$2:$B${n_dados}", f"$A$2:$A${n_dados}", f"$C$2:$C${n_dados}",
           f"$E$2:$E${n_dados}")
    seg = ""
    if segmento:
        # Corporate nao e uma secao: e o segmento "Coletivo Empresarial" dentro do
        # medico-hospitalar. Sem este filtro a formula somaria a carteira inteira.
        seg = f',{aba}!$D$2:$D${n_dados},"{segmento}"'

    def base(a, m):
        return (f"SUMPRODUCT(SUMIFS({aba}!{lim[3]},{aba}!{lim[0]},"
                f'"{secao_rotulo}",{aba}!{lim[1]},DATE({a},{m},1),'
                f"{aba}!{lim[2]},{{{regs}}}{seg}))")

    return f"=({base(fa, fm)}-{base(ia, im)})/1000"


def excel(caminho: str, df_longo, tabelas=None):
    """Abas de dados (uma por dimensao) + a aba Tabelas por SUMIFS.

    Usa xlsxwriter, nao openpyxl. Motivo medido em 02/10/2026: formatar celula a
    celula um milhao de linhas levava 25 MINUTOS a 95% de CPU — mais tempo do que
    a coleta inteira. O xlsxwriter aplica formato por COLUNA (`set_column`), sem
    tocar em cada celula, e grava a mesma planilha em segundos.
    """
    import pandas as pd

    presentes = (set(df_longo["dimensao"].dropna()) if "dimensao" in df_longo
                 else set()) | {"Contratação"}
    with pd.ExcelWriter(caminho, engine="xlsxwriter",
                        datetime_format="mmm-yy") as w:
        wb = w.book
        f_cab = wb.add_format({"bold": True, "font_color": "#FFFFFF",
                               "bg_color": VINHO, "border": 1,
                               "border_color": "#FFFFFF"})
        f_data = wb.add_format({"num_format": "mmm-yy"})
        f_num = wb.add_format({"num_format": "#,##0"})
        n = 2
        for dim in ("Contratação", "Faixa etária", "UF"):
            if dim not in presentes:
                continue
            g = _dados_longos(df_longo, dim)
            if not len(g):
                continue
            aba = ABAS[dim][0][:31]
            g.to_excel(w, sheet_name=aba, index=False)
            ws = w.sheets[aba]
            ws.hide_gridlines(2)
            ws.freeze_panes(1, 0)
            for c, nome in enumerate(g.columns):
                ws.write(0, c, nome, f_cab)
            ws.set_column(0, 0, 10, f_data)                 # data
            ws.set_column(1, 1, 20)                         # secao
            ws.set_column(2, 2, 11)                         # registro
            ws.set_column(3, len(g.columns) - 2, 24)        # quebra(s)
            ws.set_column(len(g.columns) - 1, len(g.columns) - 1, 16, f_num)
            if dim == "Contratação":
                n = len(g) + 1

        if tabelas:
            _aba_tabelas(wb, w, tabelas, n)
        _leia_me_x(wb)
    return caminho


def _aba_tabelas(wb, w, tabelas, n):
    """As tabelas do e-mail, por SUMIFS, com a mesma escala de cor."""
    ws = wb.add_worksheet("Tabelas")
    w.sheets["Tabelas"] = ws
    ws.hide_gridlines(2)
    f_cab = wb.add_format({"bold": True, "font_color": "#FFFFFF",
                           "bg_color": VINHO, "border": 1,
                           "border_color": "#FFFFFF", "align": "center",
                           "font_size": 9})
    f_g = wb.add_format({"bold": True, "font_size": 9, "border": 1,
                         "border_color": "#E3E3E3"})
    f_i = wb.add_format({"font_size": 9, "indent": 2, "border": 1,
                         "border_color": "#E3E3E3"})
    f_ng = wb.add_format({"bold": True, "num_format": "#,##0", "font_size": 9,
                          "border": 1, "border_color": "#E3E3E3"})
    f_ni = wb.add_format({"num_format": "#,##0", "font_size": 9, "border": 1,
                          "border_color": "#E3E3E3"})
    f_pg = wb.add_format({"bold": True, "num_format": "0.0%", "font_size": 9,
                          "border": 1, "border_color": "#E3E3E3"})
    f_pi = wb.add_format({"num_format": "0.0%", "font_size": 9, "border": 1,
                          "border_color": "#E3E3E3"})
    linha = 1
    for t, titulo in tabelas:
        rot = ROTULO_SECAO.get(t["secao"], t["secao"])
        segm = ("Coletivo Empresarial"
                if str(t["secao"]).startswith("corporate") else None)
        cabs = [titulo, "Lives"] + list(t["colunas"]) + ["MoM", "YoY"]
        for j, txt in enumerate(cabs):
            ws.write(linha, 1 + j, txt, f_cab)
        linha += 1
        ini = linha
        for l in t["linhas"]:
            topo = l["nivel"] == 0
            ws.write(linha, 1, l["rotulo"], f_g if topo else f_i)
            ws.write(linha, 2, l["lives"], f_ng if topo else f_ni)
            for k, per in enumerate(t.get("periodos", [])):
                f = _formula_sumifs(rot, l.get("registros") or [], per, n,
                                    segmento=segm)
                cel = f_ng if topo else f_ni
                if f:
                    ws.write_formula(linha, 3 + k, f, cel, l["net_adds"][k])
                else:
                    ws.write(linha, 3 + k, l["net_adds"][k], cel)
            base = 3 + len(t["colunas"])
            ws.write(linha, base, l["mom"], f_pg if topo else f_pi)
            ws.write(linha, base + 1, l["yoy"], f_pg if topo else f_pi)
            linha += 1
        ws.conditional_format(ini, 3, linha - 1, 3 + len(t["colunas"]) + 1,
                              {"type": "3_color_scale",
                               "min_color": "#F796A2", "mid_type": "num",
                               "mid_value": 0, "mid_color": "#FFFFFF",
                               "max_color": "#8CD6A0"})
        linha += 2
    ws.set_column(0, 0, 2)
    ws.set_column(1, 1, 26)
    ws.set_column(2, 11, 9)


def _leia_me_x(wb):
    """Aba curta com as duas armadilhas da base — as duas sao silenciosas."""
    ws = wb.add_worksheet("Leia-me")
    ws.hide_gridlines(2)
    forte = wb.add_format({"bold": True, "font_size": 11})
    normal = wb.add_format({"font_size": 10})
    txt = [
        ("ANS — Net Adds · Sala de Situação", forte), ("", normal),
        ("Fonte: painel Sala de Situação da ANS (abas Setor e Operadoras).", normal),
        ("NÃO usa o Caderno 2.0 — a ideia é pegar o dado antes dele.", normal),
        ("", normal), ("Duas armadilhas:", forte), ("", normal),
        ("1. O registro 0 é o MERCADO (total do setor), não uma operadora.", normal),
        ("   Somar a coluna inteira conta o mercado duas vezes.", normal),
        ("   Para somar operadoras, filtre registro <> 0.", normal), ("", normal),
        ("2. A soma das operadoras NÃO chega ao mercado: fica ~6% abaixo no", normal),
        ("   médico-hospitalar e ~2,7% no odontológico, estável em todos os", normal),
        ("   meses. Não é falha da coleta — operadora a operadora o número bate", normal),
        ("   exato com o painel. É o agregado da ANS que é maior que a soma das", normal),
        ("   séries que ele mesmo publica.", normal), ("", normal),
        ("As abas de quebra (Faixa etária, UF) são MARGINAIS do mesmo total e", normal),
        ("não se somam com a de contratação. Faixa etária existe só por", normal),
        ("operadora e só para o mês corrente.", normal),
    ]
    for i, (linha, fmt) in enumerate(txt):
        ws.write(i, 0, linha, fmt)
    ws.set_column(0, 0, 78)


def zipar(caminho: str) -> str:
    """Compacta a planilha. A base completa passa de 23 MB e o Gmail corta em 25;
    medido: 23,8 -> 11,9 MB."""
    import os
    import zipfile
    destino = os.path.splitext(caminho)[0] + ".zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(caminho, os.path.basename(caminho))
    return destino


def _leia_me(wb):
    """Uma aba curta explicando as duas armadilhas da base.

    Existe porque as duas sao silenciosas: somar a aba inteira parece certo e
    devolve o mercado em dobro, e somar as operadoras parece certo e fica 6%
    abaixo do mercado. Quem abrir a planilha daqui a um ano nao vai lembrar."""
    from openpyxl.styles import Alignment, Font
    ws = wb.create_sheet("Leia-me", 0)
    ws.sheet_view.showGridLines = False
    linhas = [
        ("ANS — Net Adds · Sala de Situação", True),
        ("", False),
        ("Fonte: painel Sala de Situação da ANS (abas Setor e Operadoras).", False),
        ("NÃO usa o Caderno 2.0 — a ideia é pegar o dado antes dele.", False),
        ("", False),
        ("Duas armadilhas:", True),
        ("", False),
        ("1. O registro 0 é o MERCADO (total do setor), não uma operadora.", False),
        ("   Somar a coluna inteira conta o mercado duas vezes.", False),
        ("   Para somar operadoras, filtre registro <> 0.", False),
        ("", False),
        ("2. A soma das operadoras NÃO chega ao mercado: fica ~6% abaixo no", False),
        ("   médico-hospitalar e ~2,7% no odontológico, de forma estável em", False),
        ("   todos os meses. Não é falha da coleta — operadora a operadora o", False),
        ("   número bate exato com o painel. É o próprio agregado da ANS que", False),
        ("   é maior que a soma das séries que ele mesmo publica.", False),
        ("", False),
        ("As abas de quebra (Faixa etária, UF) são MARGINAIS do mesmo total:", False),
        ("não devem ser somadas junto com Tipo de contratação.", False),
        ("Faixa etária existe só por operadora e só para o mês corrente.", False),
    ]
    for i, (txt, forte) in enumerate(linhas, start=1):
        c = ws.cell(row=i, column=1, value=txt)
        c.font = Font(bold=forte, size=12 if i == 1 else 10)
        c.alignment = Alignment(vertical="center")
    ws.column_dimensions["A"].width = 78
