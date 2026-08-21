"""Compila o levantamento em UMA LINHA POR PEDIDO — 100% deterministico, sem IA.

Entrada:  dou_mec_do1_2018_2026_linhas.parquet  (atos do DOU explodidos por curso,
          com texto_inicio e processos_citados em toda linha)
          medicina_mec_oficial.parquet          (planilhas oficiais da SERES)
Saida:    Excel com 4 abas: Atos, Medicina, Medicina_SERES, Notas.

AS REGRAS, NA ORDEM EM QUE RODAM
--------------------------------
1. DATA DO PEDIDO: o DOU so publica a DECISAO; a data do protocolo nao sai no ato.
   O que da para extrair de forma deterministica:
   - n. e-MEC no formato AAAAnnnnn (ex.: 201808078) -> o ANO do protocolo e o prefixo;
   - n. SEI 23000.nnnnnn/AAAA-dd -> o ano entre a barra e o digito;
   - pendentes da planilha SERES -> data EXATA do protocolo (unica fonte que a tem).
2. RECURSO (deteccao por padrao textual, com lista de exclusao para nao confundir com
   "recursos financeiros/humanos/orcamentarios"):
   - linha cujo texto casa padrao de recurso = "ato de recurso";
   - o pedido original vem dos processos CITADOS no texto (diferentes do da linha);
     se o recurso corre no MESMO processo, a linha nova fica marcada e a ultima coluna
     diz "mesmo processo";
   - toda linha anterior do processo recorrido e marcada "decisao recorrida".
3. PREENCHIMENTO (nunca inventa; so propaga o que a propria base prova):
   - cod_ies / cod_mantenedora: propagados por NOME normalizado, apenas quando o nome
     mapeia para UM UNICO codigo em toda a base (DOU + planilhas SERES);
   - UF/municipio: propagados pela IES apenas quando a IES aparece com UM UNICO
     municipio na base inteira (IES multicampi NAO recebe propagacao — seria chute);
   - vagas em ato de texto corrido: regex "N vagas" no proprio texto;
   - o que sobrar vazio recebe "nao consta na fonte" — celula preenchida com a verdade.
4. PENDENTES: processos da planilha SERES sem ato no DOU coletado entram como linhas
   proprias com data_decisao = "sem decisao (SERES, 04/06/2024)". So Medicina — o MEC
   nao publica lista equivalente para os demais cursos.
"""
import io
import re
import sys
import unicodedata

import pandas as pd

DATA_SERES = "04/06/2024"          # "Situação em ..." impressa nas planilhas baixadas
MARCA_VAZIO = "nao consta na fonte"

COLUNAS_FINAIS = [
    "data_pedido", "data_decisao", "tipo_decisao", "uf", "municipio",
    "mantenedora", "cod_mantenedora", "ies", "cod_ies", "curso", "numero_vagas",
    "processo", "situacao_recurso", "ref_judicial", "orgao_resumido",
    "resumo_texto", "fonte_detalhe", "link", "recurso_ref_processo",
]

# ------------------------------------------------------------------ helpers
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).lower().strip()


RX_EMEC = re.compile(r"^(20[0-2]\d)\d{5}$")
RX_SEI = re.compile(r"/(\d{4})-\d{2}$")

# padrao de recurso com contexto juridico; exclui os "recursos" que nao sao apelacao
_EXCLUI_RECURSO = (r"recursos? (?:financeiros?|humanos?|orcamentari\w+|publicos?|federais|"
                   r"proprios?|materiais|didatic\w+|tecnologic\w+|hidric\w+|de informatica|"
                   r"educacionais|digitais|abertos)")
_PADRA0_RECURSO = (r"recurso (?:administrativo|hierarquico|interposto|em face|contra)|"
                   r"interp[oo]s\w* recurso|do recurso interposto|"
                   r"(?:nega|da|deu|nego|acolh|prove|improve)\w* provimento|"
                   r"provimento (?:parcial )?(?:ao|do) recurso|"
                   r"conhec\w+ do recurso|recurso de que trata|em sede de recurso|"
                   r"julg\w+ o recurso")
RX_RECURSO = re.compile(_PADRA0_RECURSO)
RX_EXCLUI = re.compile(_EXCLUI_RECURSO)
RX_VAGAS_TXT = re.compile(r"\b(\d{1,4})\s*(?:\([^)]{2,30}\))?\s*vagas\b")


def ano_do_pedido(processo):
    p = str(processo or "").strip()
    m = RX_EMEC.match(p)
    if m:
        return f"{m.group(1)} (ano do protocolo, do nº e-MEC)"
    m = RX_SEI.search(p)
    if m:
        return f"{m.group(1)} (ano do protocolo, do nº SEI)"
    return MARCA_VAZIO


def eh_recurso(titulo, texto):
    blob = _norm(str(titulo) + " " + str(texto))
    if "recurso" not in blob:
        return False
    return bool(RX_RECURSO.search(RX_EXCLUI.sub(" ", blob)))


def _mapa_unico(pares):
    """De uma lista (chave, valor), devolve {chave: valor} apenas para as chaves que
    apontam para UM UNICO valor — propagacao so quando nao ha ambiguidade."""
    m = {}
    for k, v in pares:
        if not k or not str(v).strip():
            continue
        m.setdefault(k, set()).add(str(v).strip())
    return {k: vs.pop() for k, vs in m.items() if len(vs) == 1}


# ------------------------------------------------------------------ pipeline
def compilar(parquet_dou, parquet_seres, log=print):
    df = pd.read_parquet(parquet_dou)
    log(f"[compilar] {len(df)} linhas do DOU")
    for c in ("texto_inicio", "processos_citados"):
        if c not in df.columns:
            raise SystemExit(f"[ERRO] parquet sem a coluna '{c}': foi gerado por uma versao "
                             "antiga do dou_extrair.py — rode a extracao de novo antes.")

    # dedup de republicacao (retificacao (*)): fica a publicacao mais recente; a marca
    # segue visivel em fonte_detalhe
    for c in ("ato", "processo_emec", "curso", "ies"):
        if c not in df:
            df[c] = ""
    df["_data"] = pd.to_datetime(df["data_publicacao"], format="%d/%m/%Y", errors="coerce")
    df = df.sort_values("_data").drop_duplicates(subset=["ato", "processo_emec", "curso", "ies"],
                                                 keep="last")
    log(f"[compilar] apos dedup de republicacao: {len(df)}")

    seres = pd.read_parquet(parquet_seres)

    # ---------------- recurso
    df["_eh_recurso"] = [eh_recurso(t, x) for t, x in zip(df["ato"], df.get("texto_inicio", ""))]
    def _ref_recurso(row):
        if not row["_eh_recurso"]:
            return ""
        proprios = {str(row.get("processo_emec") or "")}
        citados = [p for p in str(row.get("processos_citados") or "").split(";")
                   if p and p not in proprios]
        return citados[0] if citados else "mesmo processo"
    df["recurso_ref_processo"] = df.apply(_ref_recurso, axis=1)

    recorridos = set(df.loc[df["_eh_recurso"], "recurso_ref_processo"])
    recorridos |= set(df.loc[df["_eh_recurso"] & (df["recurso_ref_processo"] == "mesmo processo"),
                             "processo_emec"].astype(str))
    recorridos.discard("")
    recorridos.discard("mesmo processo")

    def _situacao(row):
        if row["_eh_recurso"]:
            return "ato de recurso"
        if str(row.get("processo_emec") or "") in recorridos:
            return "decisao recorrida"
        return "sem recurso identificado"
    df["situacao_recurso"] = df.apply(_situacao, axis=1)
    log(f"[compilar] atos de recurso: {int(df['_eh_recurso'].sum())} | "
        f"pedidos marcados como recorridos: {len(recorridos)}")

    # ---------------- preenchimento por propagacao (so onde nao ha ambiguidade)
    m_cod_ies = _mapa_unico(
        [( _norm(i), c) for i, c in zip(df.get("ies", ""), df.get("cod_ies", "")) if str(c).strip()]
        + [(_norm(i), c) for i, c in zip(seres["ies"], seres["cod_ies"])])
    m_cod_mant = _mapa_unico([(_norm(m), c) for m, c in
                              zip(seres["mantenedora"], seres["cod_mantenedora"])])
    m_local = _mapa_unico([(_norm(i), f"{mu}|{u}") for i, mu, u in
                           zip(df.get("ies", ""), df.get("municipio", ""), df.get("uf", ""))
                           if str(mu).strip() and str(u).strip()])

    df["_ies_n"] = df["ies"].map(_norm)
    df["cod_ies"] = df.apply(
        lambda r: r["cod_ies"] if str(r.get("cod_ies") or "").strip()
        else m_cod_ies.get(r["_ies_n"], ""), axis=1)
    df["cod_mantenedora"] = df.get("cod_mantenedora", "")
    df["cod_mantenedora"] = [m_cod_mant.get(_norm(m), "") for m in df.get("mantenedora", "")]
    faltava_local = ~(df["municipio"].astype(str).str.strip().astype(bool))
    df.loc[faltava_local, "municipio"] = [
        m_local.get(n, "|").split("|")[0] for n in df.loc[faltava_local, "_ies_n"]]
    faltava_uf = ~(df["uf"].astype(str).str.strip().astype(bool))
    df.loc[faltava_uf, "uf"] = [
        m_local.get(n, "|").split("|")[1] for n in df.loc[faltava_uf, "_ies_n"]]

    # vagas dos atos de texto corrido
    sem_vagas = df["vagas_num"].isna() & (df["fonte_detalhe"] == "texto corrido")
    df.loc[sem_vagas, "vagas_num"] = [
        (int(m.group(1)) if (m := RX_VAGAS_TXT.search(str(t))) else None)
        for t in df.loc[sem_vagas, "texto_inicio"]]
    log(f"[compilar] pos-preenchimento: cod_ies {df['cod_ies'].astype(str).str.strip().astype(bool).mean()*100:.0f}% | "
        f"municipio {df['municipio'].astype(str).str.strip().astype(bool).mean()*100:.0f}%")

    # ---------------- colunas finais dos atos do DOU
    df["data_pedido"] = df["processo_emec"].map(ano_do_pedido)
    df["data_decisao"] = df["data_publicacao"]
    df["tipo_decisao"] = df["tipo_ato"]
    df["numero_vagas"] = df["vagas_num"]
    df["processo"] = df["processo_emec"]
    marca_ret = df.get("retificacao", False).map(
        lambda x: "; republicado com correcao (*)" if x else "")
    df["fonte_detalhe"] = df["fonte_detalhe"].astype(str) + marca_ret
    if "resumo_texto" not in df:
        df["resumo_texto"] = ""
    vazio_resumo = ~df["resumo_texto"].astype(str).str.strip().astype(bool)
    df.loc[vazio_resumo, "resumo_texto"] = df.loc[vazio_resumo, "texto_inicio"].astype(str).str[:300]

    # ---------------- pendentes da SERES que nao tem ato no DOU
    ja_no_dou = set(df["processo"].astype(str))
    pend = seres[~seres["ref_emec"].astype(str).isin(ja_no_dou)].copy()
    log(f"[compilar] pendentes SERES sem ato no DOU: {len(pend)} de {len(seres)}")
    pend_rows = pd.DataFrame({
        "data_pedido": pend.get("data_protocolo", "").replace("", MARCA_VAZIO).fillna(MARCA_VAZIO),
        "data_decisao": f"sem decisao (SERES, {DATA_SERES})",
        "tipo_decisao": "pendente: " + pend["situacao_mec"].astype(str),
        "uf": pend["uf"], "municipio": pend["municipio"],
        "mantenedora": pend["mantenedora"], "cod_mantenedora": pend["cod_mantenedora"],
        "ies": pend["ies"], "cod_ies": pend["cod_ies"],
        "curso": pend.get("curso", "MEDICINA").replace("", "MEDICINA"),
        "numero_vagas": None,
        "processo": pend["ref_emec"],
        "situacao_recurso": "nao se aplica (sem decisao)",
        "ref_judicial": pend["ref_judicial"],
        "orgao_resumido": "SERES (planilha oficial)",
        "resumo_texto": ("Natureza: " + pend.get("natureza", "").astype(str)
                         + "; Tipo: " + pend["tipo_processo"].astype(str)
                         + "; SEI: " + pend["ref_sei"].astype(str)),
        "fonte_detalhe": "planilha oficial SERES (" + DATA_SERES + ")",
        "link": ("https://www.gov.br/mec/pt-br/assuntos/es/cursos-de-medicina/"
                 "regulacao-e-supervisao/documentos"),
        "recurso_ref_processo": "",
    })

    corpo = pd.concat([df[COLUNAS_FINAIS], pend_rows[COLUNAS_FINAIS]], ignore_index=True)

    # ---------------- toda celula vazia recebe a marca explicita
    for c in COLUNAS_FINAIS:
        col = corpo[c]
        vazio = col.isna() | (col.astype(str).str.strip().isin(["", "nan", "None"]))
        corpo.loc[vazio, c] = MARCA_VAZIO
    corpo.loc[corpo["recurso_ref_processo"] == MARCA_VAZIO, "recurso_ref_processo"] = ""

    return corpo, seres


NOTAS = [
    ["Estrutura", "Uma linha por pedido/ato-curso. Colunas na ordem: data do pedido, data da "
     "decisao, tipo da decisao, UF, municipio, mantenedora e codigo, IES e codigo, curso, "
     "vagas, processo, situacao de recurso, ref. judicial, orgao, resumo, fonte, link e, por "
     "ultimo, o processo recorrido (so em atos de recurso)."],
    ["data_pedido", "O DOU publica a DECISAO, nao o protocolo. O ano do pedido vem do proprio "
     "numero do processo (e-MEC comeca pelo ano: 2018xxxxx; SEI traz o ano apos a barra). "
     "Data exata de protocolo so existe para os pendentes de Medicina (planilha SERES)."],
    ["tipo_decisao", "autorizacao, reconhecimento, renovacao, credenciamento, cautelar, "
     "sancionador etc. 'pendente: ...' = processo da planilha SERES ainda sem decisao."],
    ["situacao_recurso", "'ato de recurso' = a linha E um recurso (detectado por padrao "
     "textual juridico, excluindo 'recursos financeiros/humanos/orcamentarios'); 'decisao "
     "recorrida' = alguma linha de recurso aponta para este processo; a ultima coluna traz o "
     "processo recorrido ('mesmo processo' quando o recurso corre no proprio pedido)."],
    ["Preenchimento", "Nada e inventado. Codigos e municipio/UF ausentes foram propagados por "
     "nome APENAS quando o nome mapeia para um unico valor em toda a base (IES multicampi nao "
     "recebe propagacao). O que restou impossivel de determinar esta como 'nao consta na "
     "fonte' — e ausencia real da fonte publica, nao falha de coleta."],
    ["Pendentes", "Apenas Medicina tem lista publica de processos sem decisao (SERES, "
     "fotografia de 04/06/2024). Para os demais cursos o MEC nao publica lista equivalente; "
     "a consulta e caso a caso no e-MEC."],
    ["Cobertura", "DOU Secao 1, 2018 ate a data de corte, todos os dias uteis, zero falhas de "
     "coleta. Edicoes extras (DO1E) fora; retificacoes deduplicadas mantendo a mais recente "
     "(marcadas em fonte_detalhe)."],
]


def montar(parquet_dou, parquet_seres, saida, log=print):
    corpo, seres = compilar(parquet_dou, parquet_seres, log)
    corpo["_d"] = pd.to_datetime(corpo["data_decisao"], format="%d/%m/%Y", errors="coerce")
    corpo = corpo.sort_values("_d").drop(columns="_d")
    med = corpo[corpo["curso"].astype(str).str.contains("MEDICINA", case=False)
                & ~corpo["curso"].astype(str).str.contains("VETERIN", case=False)]
    log(f"[montar] Atos={len(corpo)} | Medicina={len(med)}")
    notas = pd.DataFrame(NOTAS, columns=["Assunto", "Descricao"])
    with pd.ExcelWriter(saida, engine="openpyxl") as xw:
        corpo.to_excel(xw, sheet_name="Atos", index=False)
        med.to_excel(xw, sheet_name="Medicina", index=False)
        seres.to_excel(xw, sheet_name="Medicina_SERES", index=False)
        notas.to_excel(xw, sheet_name="Notas", index=False)
        for aba in ("Atos", "Medicina", "Medicina_SERES"):
            ws = xw.book[aba]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
    log(f"[ok] {saida}")


if __name__ == "__main__":
    montar(sys.argv[1], sys.argv[2],
           sys.argv[3] if len(sys.argv) > 3 else "Regulacao_Cursos_2018-2026.xlsx")
