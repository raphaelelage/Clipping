"""Graficos do Funil de regulacao — nativos do Excel, 100% auditaveis.

Escreve em Regulacao_Cursos*.xlsx:
  - aba Graf_Dados: um bloco por grafico, com TITULO DIZENDO A SELECAO EXATA
    (fonte, filtro, agregacao) — o leitor confere os numeros na propria aba;
  - aba Graficos: graficos nativos (openpyxl) apontando para esses blocos.

Usa load_workbook para NAO reescrever as outras abas (preserva o amarelo do Funil).
Uso: python funil_graficos.py <arquivo.xlsx>   (ou funil_graficos.gerar(caminho))
"""
import re
import sys

import pandas as pd
from openpyxl import load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Font

RX_MED = re.compile(r"\bMEDICINA\b", re.I)

# nome da 1a coluna do bloco -> rotulo legivel do eixo X (dono, 11/09/2026)
_ROTULO_X = {
    "ano": "ano da decisao (publicacao no DOU)",
    "fase_atual": "fase atual do curso",
    "status_regulatorio": "status regulatorio do pedido",
    "uf": "UF",
    "mantenedora": "mantenedora (grupo)",
}


def _rotulo_x(coluna):
    return _ROTULO_X.get(str(coluna), str(coluna).replace("_", " "))


def _formatar_eixos(ch, eixo_y, rot_x, n_cat, decimal=False, n_series=1):
    """Eixos legiveis SEM sobreposicao (dono, 11/09/2026 — a 1a versao colidia:
    titulo do X em cima da legenda, titulo do Y em cima dos numeros, rotulo inclinado
    estourando a moldura). O que resolve cada coisa:
      - delete=False: sem isso o Excel/WPS ESCONDE o eixo inteiro;
      - majorGridlines no Y: sem a grade nao da para ler a altura da barra;
      - numFmt com milhar; decimal SO onde o valor e fracionario (fila de 4,5 anos);
      - LEGENDA REMOVIDA quando ha 1 serie so: ela repetia o titulo do eixo Y e caia
        em cima do titulo do eixo X;
      - manualLayout: reserva espaco embaixo (rotulo inclinado) e a esquerda (titulo
        do Y), que e a unica forma de o Excel nao sobrepor os textos;
      - grafico mais alto (11cm) para caber tudo."""
    from openpyxl.chart.axis import ChartLines
    from openpyxl.chart.layout import Layout, ManualLayout
    from openpyxl.chart.text import RichText
    from openpyxl.drawing.text import (Paragraph, ParagraphProperties,
                                       CharacterProperties, RichTextProperties)

    ch.height, ch.width = 11, 18

    ch.y_axis.title = eixo_y
    ch.y_axis.delete = False
    ch.y_axis.majorGridlines = ChartLines()
    ch.y_axis.numFmt = "#,##0.0" if decimal else "#,##0"
    ch.y_axis.majorTickMark = "out"

    # VALOR em cima da barra quando ha poucas categorias e 1 serie: le-se o numero
    # sem cacar a grade; barras mais largas (gapWidth menor) preenchem melhor
    if getattr(ch, "gapWidth", None) is not None:
        ch.gapWidth = 60
    if n_series == 1 and n_cat <= 12 and not decimal:
        from openpyxl.chart.label import DataLabelList
        ch.dataLabels = DataLabelList(showVal=True, numFmt="#,##0")

    ch.x_axis.title = rot_x
    ch.x_axis.delete = False
    ch.x_axis.majorTickMark = "out"
    ch.x_axis.tickLblPos = "low"

    # categoria em texto longo (fases, status, mantenedoras) ou muitas categorias
    # (27 UFs): rotulo a -45 graus, e o plot area encolhe para caber o texto embaixo
    inclinar = rot_x not in ("ano da decisao (publicacao no DOU)",) or n_cat > 14
    if inclinar:
        ch.x_axis.txPr = RichText(
            p=[Paragraph(pPr=ParagraphProperties(defRPr=CharacterProperties(sz=800)),
                         endParaRPr=CharacterProperties(sz=800))],
            bodyPr=RichTextProperties(rot="-2700000", vert="horz"))

    # 1 serie so: a legenda nao informa nada (o titulo do eixo Y ja diz) e colidia
    # com o titulo do eixo X. Com 2+ series ela fica embaixo, sem sobrepor o grafico.
    if n_series <= 1:
        ch.legend = None
    else:
        ch.legend.position = "b"
        ch.legend.overlay = False

    # plot area explicito: sobra embaixo p/ rotulo inclinado + titulo do X (+ legenda)
    baixo = 0.34 if inclinar else 0.20
    if n_series > 1:
        baixo += 0.08
    ch.layout = Layout(manualLayout=ManualLayout(
        xMode="edge", yMode="edge", x=0.13, y=0.10, w=0.84, h=max(0.40, 0.90 - baixo)))


def _grafico_dois_eixos(wsd, titulo, eixo_y, h0, h1, rot_x):
    """Grafico com eixo Y secundario: col.2 (milhares) na esquerda, col.3 (dezenas) na
    direita. Sem isso a serie pequena vira uma reta colada no zero."""
    from openpyxl.chart.axis import ChartLines
    from openpyxl.chart.layout import Layout, ManualLayout

    cats = Reference(wsd, min_col=1, min_row=h0 + 1, max_row=h1)
    c1 = LineChart()
    c1.add_data(Reference(wsd, min_col=2, max_col=2, min_row=h0, max_row=h1),
                titles_from_data=True)
    c1.set_categories(cats)
    c1.title = titulo.split(" | ")[0]
    c1.y_axis.title = eixo_y
    c1.y_axis.delete = False
    c1.y_axis.majorGridlines = ChartLines()
    c1.y_axis.numFmt = "#,##0"
    c1.x_axis.title = rot_x
    c1.x_axis.delete = False
    c1.x_axis.majorTickMark = "out"

    c2 = LineChart()
    c2.add_data(Reference(wsd, min_col=3, max_col=3, min_row=h0, max_row=h1),
                titles_from_data=True)
    c2.y_axis.axId = 200
    c2.y_axis.title = "cursos de MEDICINA (eixo direito)"
    c2.y_axis.delete = False
    c2.y_axis.numFmt = "#,##0"
    c2.y_axis.majorGridlines = None      # so uma grade, senao o fundo vira grade dupla
    c2.y_axis.crosses = "max"            # joga o 2o eixo para a direita
    c1 += c2

    c1.height, c1.width = 11, 18
    c1.legend.position = "b"
    c1.legend.overlay = False
    c1.layout = Layout(manualLayout=ManualLayout(
        xMode="edge", yMode="edge", x=0.11, y=0.10, w=0.78, h=0.58))
    return c1


def _eh_med(s):
    s = str(s or "")
    return bool(RX_MED.search(s)) and "VETERIN" not in s.upper()


def _ano(serie):
    return pd.to_datetime(serie, errors="coerce").dt.year



# ----------------------------------------------------------------- formulas do Graf_Dados
# Dono (14/09/2026): os numeros da aba Graf_Dados sao FORMULAS apontando para as abas de
# dados — auditaveis com um clique. O criterio de Medicina abaixo foi conferido contra a
# regra do codigo (\bMEDICINA\b sem VETERIN): bate 1155/1155 na aba Atos e 767/767 no
# Funil. O "medicinal" exclui QUIMICA MEDICINAL; o "biomedicina", BIOMEDICINA.
def _med_expr(rng):
    return (f'ISNUMBER(SEARCH("medicina",{rng}))'
            f'*(1-ISNUMBER(SEARCH("biomedicina",{rng})))'
            f'*(1-ISNUMBER(SEARCH("veterin",{rng})))'
            f'*(1-ISNUMBER(SEARCH("medicinal",{rng})))')


def _jud_expr(rng):
    """via judicial = ref_judicial preenchida e diferente de 'nao consta'/'nao se aplica'"""
    nao = "".join(f'*(1-ISNUMBER(SEARCH("{t}",{rng})))'
                  for t in ("nao consta", "não consta", "nao se aplica", "não se aplica"))
    return f"(LEN(TRIM({rng}))>0)" + nao


def _refs(n_funil, n_atos):
    """Intervalos fixos das abas de dados (Funil tem nota na linha 1, cabecalho na 2)."""
    f0, f1 = 3, n_funil + 2
    a0, a1 = 2, n_atos + 1
    return {
        "F_CURSO": f"Funil!$E${f0}:$E${f1}", "F_FASE": f"Funil!$H${f0}:$H${f1}",
        "F_UF": f"Funil!$F${f0}:$F${f1}", "F_STAT": f"Funil!$L${f0}:$L${f1}",
        "A_DATA": f"Atos!$B${a0}:$B${a1}", "A_TIPO": f"Atos!$C${a0}:$C${a1}",
        "A_CURSO": f"Atos!$L${a0}:$L${a1}", "A_VAGAS": f"Atos!$M${a0}:$M${a1}",
        "A_JUD": f"Atos!$P${a0}:$P${a1}", "A_MANT": f"Atos!$G${a0}:$G${a1}",
    }


def _formula(titulo, col_nome, j, r, R, variantes=None):
    """Formula da celula (coluna j do bloco, linha r do Excel). None = deixa o valor."""
    if j == 1:
        return None                      # coluna 1 e a categoria (texto)
    cat = f"$A{r}"
    med_f, med_a = _med_expr(R["F_CURSO"]), _med_expr(R["A_CURSO"])
    ano = f'(YEAR({R["A_DATA"]})={cat})'
    aut = f'({R["A_TIPO"]}="autorizacao")'
    if titulo.startswith("G1."):
        return f'=COUNTIF({R["F_FASE"]},{cat})'
    if titulo.startswith("G2."):
        return f'=SUMPRODUCT(({R["F_FASE"]}={cat})*{med_f})'
    if titulo.startswith("G3."):
        return (f'=SUMPRODUCT(({R["F_STAT"]}={cat})*{med_f}'
                f'*(LEFT({R["F_FASE"]},2)="0."))')
    if titulo.startswith("G4."):
        return (f'=SUMPRODUCT({ano}*{aut}*{med_a})' if col_nome == "Medicina"
                else f'=SUMPRODUCT({ano}*{aut}*(1-{med_a}))')
    if titulo.startswith("G5."):
        return f'=SUMPRODUCT({ano}*{aut}*{med_a}*IFERROR({R["A_VAGAS"]}*1,0))'
    if titulo.startswith("G6."):
        pend = (f'SUMPRODUCT(({R["F_UF"]}={cat})*{med_f}'
                f'*(LEFT({R["F_FASE"]},2)="0."))')
        if col_nome == "Pendentes":
            return "=" + pend
        return f'=SUMPRODUCT(({R["F_UF"]}={cat})*{med_f})-{pend}'
    if titulo.startswith("G7."):
        alvos = (variantes or {}).get(str(r), [])
        if not alvos:
            return None
        ors = "+".join(f'({R["A_MANT"]}="{v}")' for v in alvos)
        return f'=SUMPRODUCT({aut}*{med_a}*({ors}))'
    if titulo.startswith("G8."):
        jud = _jud_expr(R["A_JUD"])
        return (f'=SUMPRODUCT({ano}*{aut}*{med_a}*{jud})' if col_nome == "Via judicial"
                else f'=SUMPRODUCT({ano}*{aut}*{med_a}*(1-({jud})))')
    if titulo.startswith("G10."):
        tipo = "autorizacao" if col_nome == "Autorizado" else "indeferimento"
        return f'=SUMPRODUCT({ano}*({R["A_TIPO"]}="{tipo}")*{med_a})'
    return None                          # G9: mediana condicional fica como valor


def gerar(caminho, log=print):
    # a aba Funil tem a NOTA DE FONTES na linha 1; o cabecalho real esta na linha 2
    funil = pd.read_excel(caminho, sheet_name="Funil", header=1)
    atos = pd.read_excel(caminho, sheet_name="Atos")
    # mesma regra do funil: original superada por retificacao (*) nao conta 2x
    _tit = atos["ato"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    _bases = set(_tit[_tit.str.contains(r"\(\*\)\s*$", regex=True)]
                 .str.replace(r"\s*\(\*\)\s*$", "", regex=True))
    atos = atos[~(_tit.isin(_bases)
                  & ~_tit.str.contains(r"\(\*\)\s*$", regex=True))]
    med = funil[funil["curso"].map(_eh_med)]   # coluna medicina saiu (11/09/2026)
    aut = atos[atos["tipo_decisao"].astype(str) == "autorizacao"].copy()
    aut["_ano"] = _ano(aut["data_decisao"])
    aut["_med"] = aut["curso"].map(_eh_med)
    aut["_vagas"] = pd.to_numeric(aut["numero_vagas"], errors="coerce")
    aut["_jud"] = ~aut["ref_judicial"].astype(str).str.strip().str.lower().isin(
        ["", "nan", "nao consta na fonte", "não consta na fonte",
         "nao se aplica", "não se aplica"])
    aut["_ano_pedido"] = pd.to_numeric(
        aut["processo"].astype(str).str.extract(r"^2013(\d{2})|^(\d{4})")
            .bfill(axis=1).iloc[:, 0], errors="coerce")
    # processo e-MEC: 20XXXXXXXX — os 4 primeiros digitos sao o ano do protocolo
    aut["_ano_pedido"] = pd.to_numeric(
        aut["processo"].astype(str).str.slice(0, 4), errors="coerce")
    aut.loc[~aut["_ano_pedido"].between(2000, 2026), "_ano_pedido"] = pd.NA

    # quanto a FORMULA contaria a mais por nao saber descontar a retificacao (o calculo
    # abaixo desconta; a formula na celula nao tem como). Vai declarado no titulo.
    _t_all = pd.read_excel(caminho, sheet_name="Atos")["ato"].astype(str).str.strip() \
        .str.replace(r"\s+", " ", regex=True)
    _bases2 = set(_t_all[_t_all.str.contains(r"\(\*\)\s*$", regex=True)]
                  .str.replace(r"\s*\(\*\)\s*$", "", regex=True))
    _sup = _t_all.isin(_bases2) & ~_t_all.str.contains(r"\(\*\)\s*$", regex=True)
    _n_sup = int(_sup.sum())
    AVISO_RET = (f" | NOTA DA FORMULA: a formula desta tabela varre a aba Atos inteira e "
                 f"por isso conta tambem o ato ORIGINAL que depois foi republicado com "
                 f"correcao (*) — {_n_sup} linha(s) na base. O numero pode ficar "
                 f"ligeiramente acima do que se ve nos relatorios que descontam a "
                 f"republicacao.")

    blocos = []   # (titulo_com_selecao, DataFrame[categoria, series...], tipo, eixo_y)

    t1 = (funil.groupby("fase_atual").size().rename("cursos").reset_index()
               .sort_values("fase_atual"))
    blocos.append(("G1. Cursos por fase atual — TODOS | fonte: aba Funil, "
                   "contagem de linhas por fase_atual", t1, "bar", "cursos"))

    t2 = (med.groupby("fase_atual").size().rename("cursos").reset_index()
             .sort_values("fase_atual"))
    blocos.append(("G2. Cursos por fase atual — MEDICINA | fonte: aba Funil, "
                   "medicina='Sim'", t2, "bar", "cursos"))

    pend = med[med["fase_atual"].astype(str).str.startswith("0.")]
    t3 = (pend.groupby("status_regulatorio").size().rename("cursos")
              .reset_index().sort_values("cursos", ascending=False))
    blocos.append(("G3. Pendentes de MEDICINA por status regulatorio | fonte: aba "
                   "Funil, medicina='Sim' e fase 0.x — sem estimativa de vagas "
                   "(pendente nao tem vagas definidas)", t3, "bar", "cursos"))

    t4 = (aut.groupby(["_ano", "_med"]).size().unstack(fill_value=0)
             .rename(columns={True: "Medicina", False: "Demais cursos"})
             .reset_index().rename(columns={"_ano": "ano"}).dropna(subset=["ano"]))
    t4["ano"] = t4["ano"].astype(int)
    blocos.append(("G4. Autorizacoes por ano (n. de cursos) | fonte: aba Atos, "
                   "tipo_decisao='autorizacao', ano de data_decisao; Medicina = "
                   "\\bMEDICINA\\b sem VETERIN. Medicina tem EIXO PROPRIO (direita): "
                   "sao dezenas contra milhares — no mesmo eixo a linha ficava colada "
                   "no zero e nao dava para ler a tendencia",
                   t4[["ano", "Demais cursos", "Medicina"]],
                   "line_dual", "cursos autorizados (demais)"))

    # ANOS COMPLETOS nos graficos de Medicina: sem isto a serie MORRE em 2021 e parece
    # dado faltando — quando o fato e a MORATORIA (zero autorizacao nova de Medicina de
    # 2022 a 2026: ADC 81 exige chamamento e o edital de 2023 foi revogado em fev/2026).
    # Com o zero explicito, o "precipicio" fica visivel no grafico.
    anos_todos = sorted(int(x) for x in aut["_ano"].dropna().unique())

    def _completa(df, col_ano="ano"):
        base = pd.DataFrame({col_ano: anos_todos})
        out = base.merge(df, on=col_ano, how="left")
        for c in out.columns:
            if c != col_ano:
                out[c] = out[c].fillna(0)
        return out

    t5 = (aut[aut["_med"]].groupby("_ano")["_vagas"].sum().rename("vagas")
          .reset_index().rename(columns={"_ano": "ano"}).dropna())
    t5["ano"] = t5["ano"].astype(int)
    t5 = _completa(t5)
    blocos.append(("G5. Vagas de MEDICINA autorizadas por ano | fonte: aba Atos, "
                   "tipo_decisao='autorizacao', soma de numero_vagas (so onde o ato "
                   "informa vagas). ZERO em 2022-2026 e REAL: moratoria (ADC 81 exige "
                   "chamamento publico; edital 2023 revogado pela Portaria MEC 129/2026)",
                   t5, "bar", "vagas"))

    t6 = med.assign(_pend=med["fase_atual"].astype(str).str.startswith("0."))
    t6 = (t6.groupby(["uf", "_pend"]).size().unstack(fill_value=0)
            .rename(columns={False: "Decididos", True: "Pendentes"}).reset_index())
    t6 = t6[t6["uf"].astype(str).str.strip().ne("")].sort_values(
        "Decididos", ascending=False)
    blocos.append(("G6. MEDICINA por UF — decididos x pendentes | fonte: aba Funil, "
                   "medicina='Sim', contagem por uf", t6, "bar", "cursos"))

    t7 = aut[aut["_med"]].copy()
    # mesma mantenedora com 2+ grafias ("SER EDUCACIONAL S.A." x "Ser Educacional
    # S.A.") dividia a contagem do top-15: agrupa por grafia normalizada e exibe a
    # grafia mais frequente do grupo
    def _nmant(x):
        import unicodedata as _u
        x = _u.normalize("NFKD", str(x or ""))
        x = "".join(c for c in x if not _u.combining(c)).upper()
        return " ".join(x.replace(".", "").replace(",", "").split())
    # python puro: NA do arrow atravessava filtro/mode e explodia (2 crashes em
    # 11/09/2026) — aqui nada de semantica de NA
    from collections import Counter
    _originais = [("" if v is None or (isinstance(v, float)) else str(v).strip())
                  for v in t7["mantenedora"].tolist()]
    _vazios = {"", "nan", "none", "<na>", "nao consta na fonte"}
    _cont = {}
    for o in _originais:
        if o.lower() in _vazios:
            continue
        _cont.setdefault(_nmant(o), Counter())[o] += 1
    _rep = {k: c.most_common(1)[0][0] for k, c in _cont.items()}
    t7["mantenedora"] = [_rep.get(_nmant(o), "") if o.lower() not in _vazios else ""
                         for o in _originais]
    t7 = t7[t7["mantenedora"] != ""]
    _variantes_por_nome = {rep: sorted(c) for k, c in _cont.items()
                           for rep in [_rep[k]]}
    t7 = (t7.groupby("mantenedora").size().rename("autorizacoes").reset_index()
            .sort_values("autorizacoes", ascending=False).head(15))
    blocos.append(("G7. Top 15 mantenedoras em AUTORIZACOES de Medicina 2018-2026 | "
                   "fonte: aba Atos, tipo_decisao='autorizacao' e curso Medicina",
                   t7, "bar", "autorizacoes"))

    t8 = (aut[aut["_med"]].groupby(["_ano", "_jud"]).size().unstack(fill_value=0)
          .rename(columns={True: "Via judicial", False: "Via ordinaria"})
          .reset_index().rename(columns={"_ano": "ano"}).dropna(subset=["ano"]))
    t8["ano"] = t8["ano"].astype(int)
    for col in ("Via ordinaria", "Via judicial"):     # colunas fixas: se um ano nao tem
        if col not in t8.columns:                     # aquela via, a serie ainda aparece
            t8[col] = 0
    t8 = _completa(t8[["ano", "Via ordinaria", "Via judicial"]])
    blocos.append(("G8. Autorizacoes de MEDICINA por via e ano | fonte: aba Atos, "
                   "tipo_decisao='autorizacao', via judicial = ref_judicial preenchida "
                   "no ato. ZERO em 2022-2026 e REAL (moratoria) — os atos de Medicina "
                   "desses anos sao reconhecimento/renovacao de cursos ja existentes",
                   t8, "bar_stack", "cursos"))

    # G10: APROVADO x NEGADO (dono, 10/09/2026) — so existe depois da correcao do
    # classificador; antes o indeferimento vinha rotulado como autorizacao e a taxa de
    # rejeicao era invisivel (em 2024 houve MAIS negativas que aprovacoes em Medicina)
    dec = atos[atos["tipo_decisao"].astype(str).isin(["autorizacao", "indeferimento"])].copy()
    dec["_ano"] = _ano(dec["data_decisao"])
    dec["_med"] = dec["curso"].map(_eh_med)
    t10 = (dec[dec["_med"]].groupby(["_ano", "tipo_decisao"]).size().unstack(fill_value=0)
           .rename(columns={"autorizacao": "Autorizado", "indeferimento": "Indeferido"})
           .reset_index().rename(columns={"_ano": "ano"}).dropna(subset=["ano"]))
    if len(t10):
        t10["ano"] = t10["ano"].astype(int)
        for col in ("Autorizado", "Indeferido"):
            if col not in t10.columns:
                t10[col] = 0
        t10 = _completa(t10[["ano", "Autorizado", "Indeferido"]])
        blocos.append(("G10. MEDICINA: pedidos AUTORIZADOS x INDEFERIDOS por ano | fonte: "
                       "aba Atos, tipo_decisao autorizacao vs indeferimento, curso Medicina "
                       "(indeferimento de AUMENTO DE VAGAS fica fora: e pedido acessorio "
                       "de curso existente, tipo indeferimento_aditamento)",
                       t10, "bar", "atos"))

    aut["_fila"] = aut["_ano"] - aut["_ano_pedido"]
    t9 = (aut[aut["_fila"].between(0, 15)]
          .groupby(["_ano", "_med"])["_fila"].median().unstack()
          .rename(columns={True: "Medicina", False: "Demais cursos"})
          .reset_index().rename(columns={"_ano": "ano"}).dropna(subset=["ano"]))
    t9["ano"] = t9["ano"].astype(int)
    blocos.append(("G9. Fila mediana ate a autorizacao (anos entre o ano do processo "
                   "e-MEC e o ano da decisao) | fonte: aba Atos, "
                   "tipo_decisao='autorizacao'; ano do pedido = 4 primeiros digitos "
                   "do n. do processo", t9, "line", "anos de fila"))

    # ---------------- escrita: openpyxl em cima do arquivo existente ----------------
    wb = load_workbook(caminho)
    for aba in ("Graf_Dados", "Graficos"):
        if aba in wb.sheetnames:
            del wb[aba]
    _R = _refs(len(funil), len(pd.read_excel(caminho, sheet_name="Atos")))
    _DA_ATOS = ("G4.", "G5.", "G7.", "G8.", "G9.", "G10.")
    blocos = [((t + AVISO_RET) if t.startswith(_DA_ATOS) else t, d, k, y)
              for t, d, k, y in blocos]
    wsd = wb.create_sheet("Graf_Dados")
    wsg = wb.create_sheet("Graficos")
    negrito = Font(bold=True)

    linha, ancoras = 1, []
    for titulo, df, tipo, eixo_y in blocos:
        wsd.cell(row=linha, column=1, value=titulo).font = negrito
        head = linha + 1
        for j, c in enumerate(df.columns, start=1):
            wsd.cell(row=head, column=j, value=str(c)).font = negrito
        _var_map = {}
        if titulo.startswith("G7."):
            for _i, (_, _r) in enumerate(df.iterrows(), start=1):
                _var_map[str(head + _i)] = _variantes_por_nome.get(
                    str(_r.iloc[0]), [str(_r.iloc[0])])
        for i, (_, r) in enumerate(df.iterrows(), start=1):
            for j, v in enumerate(r.tolist(), start=1):
                _f = _formula(titulo, str(df.columns[j - 1]), j, head + i, _R, _var_map)
                if _f is not None:
                    wsd.cell(row=head + i, column=j, value=_f)
                    continue
                wsd.cell(row=head + i, column=j,
                         value=(None if pd.isna(v) else
                                (float(v) if isinstance(v, (int, float)) else str(v))))
        # a 1a coluna do bloco E a categoria do eixo X — o rotulo sai dela (dono,
        # 11/09/2026: "quero ver o eixo x com o que quer dizer no eixo x")
        # valor fracionario (ex.: fila mediana de 4,5 anos) nao pode usar formato de
        # inteiro no eixo — "#,##0" exibiria 5 e mentiria sobre a mediana
        vals = pd.to_numeric(df.iloc[:, 1:].stack(), errors="coerce").dropna()
        decimal = bool(len(vals)) and not bool((vals % 1 == 0).all())
        ancoras.append((titulo, tipo, eixo_y, head, head + len(df), df.shape[1],
                        _rotulo_x(df.columns[0]), len(df), decimal))
        linha = head + len(df) + 3

    # 11cm de altura ~ 23 linhas: ancoras espacadas 24 linhas para os
    # graficos nao se sobreporem na aba
    pos = ["A1", "L1", "A25", "L25", "A49", "L49", "A73", "L73",
           "A97", "L97", "A121", "L121"]
    for k, (titulo, tipo, eixo_y, h0, h1, ncols, rot_x, n_cat, dec) in enumerate(ancoras):
        if tipo == "line_dual":
            # duas ordens de grandeza no mesmo grafico (milhares x dezenas): a 2a serie
            # ganha eixo proprio a direita, senao fica achatada no zero. Cada eixo diz
            # a que serie pertence, para a leitura nao confundir as escalas.
            wsg.add_chart(_grafico_dois_eixos(wsd, titulo, eixo_y, h0, h1, rot_x), pos[k])
            continue
        ch = LineChart() if tipo == "line" else BarChart()
        if tipo == "bar_stack":
            ch.type, ch.grouping, ch.overlap = "col", "stacked", 100
        elif tipo == "bar":
            ch.type = "col"
        ch.title = titulo.split(" | ")[0]
        # sem "variar cores por ponto": com 1 serie so, o Excel/WPS pinta cada barra de
        # uma cor e joga as CATEGORIAS na legenda (parecia que os anos eram as series)
        ch.varyColors = False
        dados = Reference(wsd, min_col=2, max_col=ncols, min_row=h0, max_row=h1)
        cats = Reference(wsd, min_col=1, min_row=h0 + 1, max_row=h1)
        ch.add_data(dados, titles_from_data=True)
        ch.set_categories(cats)
        _formatar_eixos(ch, eixo_y, rot_x, n_cat, dec, ncols - 1)
        wsg.add_chart(ch, pos[k])

    wsd.freeze_panes = "A2"
    wb.save(caminho)
    log(f"[graficos] {len(blocos)} graficos + dados auditaveis gravados "
        f"(abas Graficos e Graf_Dados)")


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx")
