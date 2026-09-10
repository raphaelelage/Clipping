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


def _eh_med(s):
    s = str(s or "")
    return bool(RX_MED.search(s)) and "VETERIN" not in s.upper()


def _ano(serie):
    return pd.to_datetime(serie, errors="coerce").dt.year


def gerar(caminho, log=print):
    # a aba Funil tem a NOTA DE FONTES na linha 1; o cabecalho real esta na linha 2
    funil = pd.read_excel(caminho, sheet_name="Funil", header=1)
    atos = pd.read_excel(caminho, sheet_name="Atos")
    med = funil[funil["medicina"].astype(str) == "Sim"]
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
                   "\\bMEDICINA\\b sem VETERIN", t4[["ano", "Medicina", "Demais cursos"]],
                   "line", "cursos autorizados"))

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
    t7["mantenedora"] = t7["mantenedora"].astype(str).str.strip()
    t7 = t7[~t7["mantenedora"].str.lower().isin(["", "nan", "nao consta na fonte"])]
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
                       "aba Atos, tipo_decisao autorizacao vs indeferimento, curso Medicina",
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
    wsd = wb.create_sheet("Graf_Dados")
    wsg = wb.create_sheet("Graficos")
    negrito = Font(bold=True)

    linha, ancoras = 1, []
    for titulo, df, tipo, eixo_y in blocos:
        wsd.cell(row=linha, column=1, value=titulo).font = negrito
        head = linha + 1
        for j, c in enumerate(df.columns, start=1):
            wsd.cell(row=head, column=j, value=str(c)).font = negrito
        for i, (_, r) in enumerate(df.iterrows(), start=1):
            for j, v in enumerate(r.tolist(), start=1):
                wsd.cell(row=head + i, column=j,
                         value=(None if pd.isna(v) else
                                (float(v) if isinstance(v, (int, float)) else str(v))))
        ancoras.append((titulo, tipo, eixo_y, head, head + len(df), df.shape[1]))
        linha = head + len(df) + 3

    pos = ["A1", "J1", "A20", "J20", "A39", "J39", "A58", "J58", "A77", "J77",
           "A96", "J96"]
    for k, (titulo, tipo, eixo_y, h0, h1, ncols) in enumerate(ancoras):
        ch = LineChart() if tipo == "line" else BarChart()
        if tipo == "bar_stack":
            ch.type, ch.grouping, ch.overlap = "col", "stacked", 100
        elif tipo == "bar":
            ch.type = "col"
        ch.title = titulo.split(" | ")[0]
        ch.y_axis.title = eixo_y
        ch.height, ch.width = 8.5, 16
        # sem "variar cores por ponto": com 1 serie so, o Excel/WPS pinta cada barra de
        # uma cor e joga as CATEGORIAS na legenda (parecia que os anos eram as series)
        ch.varyColors = False
        dados = Reference(wsd, min_col=2, max_col=ncols, min_row=h0, max_row=h1)
        cats = Reference(wsd, min_col=1, min_row=h0 + 1, max_row=h1)
        ch.add_data(dados, titles_from_data=True)
        ch.set_categories(cats)
        wsg.add_chart(ch, pos[k])

    wsd.freeze_panes = "A2"
    wb.save(caminho)
    log(f"[graficos] {len(blocos)} graficos + dados auditaveis gravados "
        f"(abas Graficos e Graf_Dados)")


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx")
