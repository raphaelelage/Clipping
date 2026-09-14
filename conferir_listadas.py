"""Aba "Conferir - Listadas": o que as EMPRESAS ABERTAS comunicaram ao mercado sobre
vagas/autorizacao de Medicina x o que a base tem do DOU.

Pedido do dono (14/09/2026): "veja os fatos relevantes/comunicados ao mercado delas
sobre autorizacao de vagas; cruze com o que temos na base e veja se tem algo que nao
faca sentido (se falta alguma, se tem alguma que nao mapeamos, se tem alguma
inconsistencia no numero do scrapper vs o que tem nos filings)".

FONTE (gratuita, oficial, deterministica): dataset IPE da CVM — todos os Fatos
Relevantes e Comunicados ao Mercado protocolados, com link para o PDF original.
    https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/
Empresas de educacao na B3. AFYA e VITRU (Nasdaq) nao entregam IPE a CVM: o que a CVM
tem e so da VITRU EDUCACAO S.A.; os 6-K da Afya ficam FORA deste cruzamento — esta
declarado na propria aba para nao passar por cobertura completa.

O cruzamento e feito pelo NUMERO DA PORTARIA, que todo comunicado cita ("a SERES,
atraves da Portaria n. 414 de 15 de agosto de 2024, deferiu..."), contra a coluna `ato`
da aba Atos. Nada e inferido: o que o documento nao disser fica vazio.

Uso:  python conferir_listadas.py [Regulacao_Cursos.xlsx]
Tempo: ~2 min (baixa 4 anos de IPE + os PDFs dos comunicados que falam de vagas).
"""
import io
import os
import re
import sys
import unicodedata as ud
import zipfile

import pandas as pd
import requests

IPE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{}.zip"
ANOS = (2022, 2023, 2024, 2025, 2026)
ABA = "Conferir - Listadas"
# nome da companhia no IPE -> como o dono chama
LISTADAS = {"YDUQS": "YDUQS", "COGNA": "COGNA", "SER EDUCACIONAL": "SER EDUCACIONAL",
            "ANIMA HOLDING": "ANIMA", "CRUZEIRO DO SUL EDUCACIONAL": "CRUZEIRO DO SUL",
            "VITRU": "VITRU", "BAHEMA EDUCACAO": "BAHEMA"}
# marcas de IES por grupo (para o bloco de cursos sem ato)
# ORDEM IMPORTA: a marca mais especifica vem primeiro. "ITPAC CRUZEIRO DO SUL" e da
# AFYA — "Cruzeiro do Sul" ali e a CIDADE no Acre, nao o grupo (erro pego em 14/09/2026).
GRUPOS = {
    "AFYA": r"AFYA|ITPAC|UNIPTAN|IESVAP|UNIREDENTOR",
    "YDUQS": r"ESTACIO|IDOMED|UNIFAMETRO|IBMEC|WYDEN|UNITOLEDO|UNIFAVIP|UNIFANOR|UNIFBV",
    "COGNA": r"ANHANGUERA|PITAGORAS|UNOPAR|UNIDERP|UNIME|KROTON",
    "ANIMA": r"UNIBH|SOCIESC|UNICURITIBA|SAO JUDAS|UNIFG|MILTON CAMPOS|UNIFACS|FASEH|"
             r"UNIRITTER|AGES|INSPIRALI",
    "SER EDUCACIONAL": r"UNINASSAU|UNAMA|UNINABUCO|JOAQUIM NABUCO|UNIVERITAS|UNESC",
    "CRUZEIRO DO SUL": r"CRUZEIRO DO SUL|CEUNSP|CESUCA|POSITIVO|BRAZ CUBAS|MODULO|"
                       r"SERRA GAUCHA|UNIFRAN|UNICID",
    "VITRU": r"UNICESUMAR|UNIASSELVI",
}
RX_MED = re.compile(r"\bMEDICINA\b")   # borda de palavra: BIOMEDICINA contem MEDICINA
RX_NAO_ATO = re.compile(r"supremo|STF|judicial|aquisi[çc]|capital", re.I)
RX_DECISAO = re.compile(r"autoriz|deferi|expans|aumento|acr[eé]scim", re.I)
RX_ASSUNTO = re.compile(r"vaga|medicin|autoriz|credenciam|mais m[eé]dicos|chamamento", re.I)
RX_PORT = re.compile(r"portarias?\s*n?[^\dA-Za-z]{0,5}(\d{1,4})"
                     r"(?:[^\d]{0,20}?(\d{1,2})\s+de\s+([a-zA-Zç]+)\s+de\s+(\d{4}))?", re.I)
RX_ATO_NUM = re.compile(r"(?:PORTARIA|PROTARIA)[^\d]{0,25}?(\d{1,4})", re.I)
RX_VAGAS = (re.compile(r"(?:autoriza[çc][ãa]o|expans[ãa]o|aumento|acr[ée]scimo)\s+de\s+"
                       r"(\d{1,4})\s*(?:\([^)]{0,40}\)\s*)?vagas", re.I),
            re.compile(r"(\d{1,4})\s*\([^)]{0,40}\)\s*vagas", re.I),
            re.compile(r"(\d{1,4})\s+vagas\s+anuais\s+adicionais", re.I))
RX_MUN = (re.compile(r"munic[íi]pio\s+de\s+([A-Za-zÀ-ÿ'\s]{3,30}?)\s*[,.]", re.I),
          re.compile(r"cidade\s+de\s+([A-Za-zÀ-ÿ'\s]{3,30}?)\s*[,.]", re.I))
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
         "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11,
         "dezembro": 12}
# calibrado em 14/09/2026 na propria base: Medicina AUTORIZADA de 2018 em diante tem
# mediana de cod_curso 1,41M (2018) a 1,61M (2024). Abaixo de 1,4M o curso provavelmente
# foi autorizado ANTES de 2018 — fora da janela da base, nao e buraco de cobertura.
COD_RECENTE = 1_400_000
COLS = ["bloco", "grupo", "data", "assunto_ou_curso", "portaria_citada", "vagas_citadas",
        "municipio_citado", "o_que_a_base_tem", "situacao", "o_que_conferir", "fonte"]
NOTA = (
    "CONFERIR - LISTADAS: fatos relevantes e comunicados ao mercado das empresas ABERTAS "
    "de educacao sobre autorizacao/aumento de vagas, cruzados com os atos do DOU que a "
    "base tem. Fonte dos comunicados: dataset IPE da CVM (link na ultima coluna leva ao "
    "PDF original). O cruzamento e pelo NUMERO DA PORTARIA que o proprio comunicado cita. "
    "Regerar com: python conferir_listadas.py <arquivo>. ATENCAO: Afya e Vitru sao "
    "listadas na Nasdaq e nao protocolam IPE na CVM — os 6-K delas NAO entram aqui.")


def nm(s):
    s = ud.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not ud.combining(c)).upper().strip()


def v(s):
    return s.fillna("").astype(str).str.strip()


def _ipe(log=print):
    """Fatos relevantes + comunicados das educacionais (dataset publico da CVM)."""
    partes = []
    for ano in ANOS:
        try:
            r = requests.get(IPE.format(ano), timeout=180)
        except Exception as e:
            log(f"[listadas] {ano}: {type(e).__name__} — pulado")
            continue
        if r.status_code != 200:
            log(f"[listadas] {ano}: sem arquivo no portal (HTTP {r.status_code})")
            continue
        z = zipfile.ZipFile(io.BytesIO(r.content))
        for arq in z.namelist():
            if arq.lower().endswith(".csv"):
                partes.append(pd.read_csv(z.open(arq), sep=";", encoding="latin-1",
                                          dtype=str, on_bad_lines="skip"))
    if not partes:
        return pd.DataFrame()
    d = pd.concat(partes, ignore_index=True)
    alvo = d["Nome_Companhia"].map(nm).str.contains("|".join(LISTADAS), regex=True, na=False)
    d = d[alvo & d["Categoria"].isin(["Fato Relevante", "Comunicado ao Mercado"])]
    d = d[v(d["Assunto"]).str.contains(RX_ASSUNTO, na=False)]
    return d.sort_values("Data_Entrega")


def _texto(link):
    import pdfplumber
    b = requests.get(link, timeout=120).content
    with pdfplumber.open(io.BytesIO(b)) as pdf:
        return " ".join(" ".join((p.extract_text() or "") for p in pdf.pages).split())


def gerar(caminho="Regulacao_Cursos.xlsx", log=print):
    xl = pd.ExcelFile(caminho)
    atos = xl.parse("Atos")
    atos["_d"] = pd.to_datetime(atos["data_decisao"], errors="coerce")
    atos["_num"] = v(atos["ato"]).map(
        lambda a: (RX_ATO_NUM.search(a).group(1) if RX_ATO_NUM.search(a) else ""))
    atos["_ano"] = atos["_d"].dt.year.astype("Int64")

    docs = _ipe(log)
    log(f"[listadas] {len(docs)} comunicado(s) sobre vagas/autorizacao na CVM")
    linhas = []
    for _, r in docs.iterrows():
        empresa = nm(r["Nome_Companhia"])
        grupo = next((g for k, g in LISTADAS.items() if k in empresa), empresa[:22])
        try:
            t = _texto(r["Link_Download"])
        except Exception as e:
            t = ""
            log(f"[listadas] PDF ilegivel ({type(e).__name__}) em {r['Assunto'][:40]}")
        # so vale a portaria que o texto liga a uma DECISAO de vagas/curso: comunicado
        # sobre o STF cita "Portaria 531/2023" de passagem e nao e ato de autorizacao
        mp = None
        # comunicado sobre STF/decisao judicial/aquisicao/capital NAO e ato de
        # autorizacao: ele cita portaria de passagem (ex.: "Portaria SERES 531/2023")
        so_contexto = bool(RX_NAO_ATO.search(str(r["Assunto"])))
        for m in ([] if so_contexto else RX_PORT.finditer(t)):
            volta = t[max(0, m.start() - 220):m.end() + 220]
            if RX_DECISAO.search(volta):
                mp = m
                break
        num = mp.group(1) if mp else ""
        dia, mes, ano = (mp.group(2), mp.group(3), mp.group(4)) if mp else (None, None, None)
        data_port = (f"{int(dia):02d}/{MESES[nm(mes).lower()]:02d}/{ano}"
                     if dia and mes and ano and nm(mes).lower() in MESES else "")
        vagas = next((m.group(1) for m in (rx.search(t) for rx in RX_VAGAS) if m), "")
        mun = next((m.group(1).strip() for m in (rx.search(t) for rx in RX_MUN) if m), "")

        cand = atos.iloc[0:0]
        if num:
            cand = atos[atos["_num"] == num]
            if ano:
                cand = cand[cand["_ano"].astype(str) == str(ano)]
            else:
                d0 = pd.Timestamp(str(r["Data_Entrega"])[:10])
                cand = cand[(cand["_d"] >= d0 - pd.Timedelta(days=60))
                            & (cand["_d"] <= d0 + pd.Timedelta(days=15))]
            med = cand[v(cand["curso"]).map(lambda c: bool(RX_MED.search(nm(c))))]
            cand = med if len(med) else cand

        vagas_base = sorted({x for x in v(cand["numero_vagas"]) if x and x != "nan"})
        tem = len(cand) > 0
        if not num:
            sit = "nao se aplica (o comunicado nao cita portaria de autorizacao)"
            fazer = ("Comunicado sobre outro assunto (decisao do STF, aquisicao, decisao "
                     "judicial). Nao ha ato do DOU para cruzar.")
        elif not tem:
            sit = "ATO NAO ENCONTRADO NA BASE"
            fazer = ("O comunicado cita uma portaria que a base nao tem. Confira no DOU "
                     "(in.gov.br) pela data citada; se o ato existir, rode a varredura "
                     "do periodo pelo app.")
        elif vagas and vagas_base and vagas not in [x.replace(".0", "") for x in vagas_base]:
            sit = "VAGAS DIVERGENTES"
            fazer = ("O numero de vagas do comunicado nao bate com o do ato. Leia o ato "
                     "no DOU: costuma ser vagas TOTAIS x ACRESCIMO.")
        else:
            sit = "confere"
            fazer = ""
        linhas.append({
            "bloco": "1. Comunicado x base", "grupo": grupo,
            "data": str(r["Data_Entrega"])[:10], "assunto_ou_curso": str(r["Assunto"]).strip(),
            "portaria_citada": (f"n. {num}" + (f" de {data_port}" if data_port else "")) if num else "",
            "vagas_citadas": vagas, "municipio_citado": mun,
            "o_que_a_base_tem": (" | ".join(sorted(set(v(cand["ato"])))[:1])
                                 + (f" | tipo: {', '.join(sorted(set(v(cand['tipo_decisao']))))}" if tem else "")
                                 + (f" | vagas: {', '.join(vagas_base)}" if vagas_base else "")) if tem else "nada",
            "situacao": sit, "o_que_conferir": fazer, "fonte": r["Link_Download"]})

    # ---- bloco 2: Medicina ativa no e-MEC das listadas SEM nenhum ato na base
    base_dir = os.path.dirname(os.path.abspath(__file__))
    pq = os.path.join(base_dir, "cursos_emec.parquet")
    if os.path.exists(pq):
        emec = pd.read_parquet(pq)
        med = emec[emec["curso"].map(nm).str.contains(RX_MED, na=False, regex=True)
                   & ~emec["curso"].map(nm).str.contains("VETERIN", na=False)
                   & emec["situacao_emec"].isin(["Em atividade", "Em extinção"])].copy()
        med["_cod"] = med["cod_curso"].astype(str).str.replace(r"\.0$", "", regex=True)
        cods = set(v(atos["cod_curso"]).str.replace(r"\.0$", "", regex=True)) - {""}
        med["_grupo"] = ""
        for g, rx in GRUPOS.items():
            hit = med["ies"].map(nm).str.contains(rx, regex=True, na=False)
            med.loc[hit & (med["_grupo"] == ""), "_grupo"] = g
        falta = med[(med["_grupo"] != "") & (~med["_cod"].isin(cods))].copy()
        falta["_n"] = pd.to_numeric(falta["_cod"], errors="coerce")
        falta = falta.sort_values("_n", ascending=False)
        for _, c in falta.iterrows():
            recente = pd.notna(c["_n"]) and c["_n"] >= COD_RECENTE
            linhas.append({
                "bloco": "2. Curso ativo sem ato na base", "grupo": c["_grupo"], "data": "",
                "assunto_ou_curso": f"{c['curso']} — {c['ies']} ({c['municipio']}/{c['uf']})",
                "portaria_citada": "", "vagas_citadas": str(c["vagas"]),
                "municipio_citado": str(c["municipio"]),
                "o_que_a_base_tem": f"nenhum ato com o cod_curso {c['_cod']}",
                "situacao": ("CODIGO RECENTE — conferir" if recente
                             else "provavelmente anterior a 2018 (fora da janela da base)"),
                "o_que_conferir": (
                    "Curso ativo no e-MEC sem nenhum ato no DOU desde 2018. Codigo alto "
                    "sugere autorizacao recente: procure o ato no DOU e rode a varredura "
                    "do periodo." if recente else
                    "A base cobre o DOU de 2018 em diante; curso autorizado antes disso "
                    "so aparece quando tiver um ato novo (renovacao, vagas). Sem acao."),
                "fonte": "Cadastro e-MEC (cursos_emec.parquet)"})

    df = pd.DataFrame(linhas, columns=COLS)
    log("[listadas] " + str(len(df)) + " linha(s): "
        + str(df["situacao"].value_counts().to_dict()))
    _gravar(caminho, xl, df, log)
    return df


def _gravar(caminho, xl, df, log=print):
    """Insere/atualiza SO a aba nova, com openpyxl. NUNCA reescrever o arquivo inteiro
    via pandas: isso apaga os graficos nativos e as notas de cabecalho das outras abas."""
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = load_workbook(caminho)
    if ABA in wb.sheetnames:
        del wb[ABA]
    ws = wb.create_sheet(ABA)
    ws.cell(row=1, column=1, value=NOTA)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLS))
    c = ws.cell(row=1, column=1)
    c.font = Font(italic=True, size=9, color="663300")
    c.alignment = Alignment(wrap_text=True, vertical="top")
    c.fill = PatternFill(start_color="FFFDF3D6", end_color="FFFDF3D6", fill_type="solid")
    ws.row_dimensions[1].height = 52
    for j, col in enumerate(COLS, start=1):
        cel = ws.cell(row=2, column=j, value=col)
        cel.font = Font(bold=True)
    for i, (_, r) in enumerate(df.iterrows(), start=3):
        for j, col in enumerate(COLS, start=1):
            ws.cell(row=i, column=j, value=("" if pd.isna(r[col]) else str(r[col])))
    for col, larg in (("A", 24), ("B", 16), ("C", 11), ("D", 58), ("E", 20),
                      ("F", 9), ("G", 18), ("H", 52), ("I", 30), ("J", 52), ("K", 40)):
        ws.column_dimensions[col].width = larg
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cel in row:
            cel.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = "A2:" + chr(64 + len(COLS)) + str(ws.max_row)
    wb.save(caminho)
    log(f"[listadas] aba '{ABA}' gravada em {caminho} ({len(df)} linhas)")


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx")
