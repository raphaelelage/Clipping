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
    "resumo_texto", "retificacao", "fonte_detalhe", "link", "recurso_ref_processo",
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
    """So o ANO do protocolo e conhecivel pelo numero do processo. Para a coluna sair com
    TIPO DE DATA no Excel, o ano vira 01/01 do ano — a aba Notas explica que dia e mes sao
    convencao. Data exata so os pendentes da SERES tem."""
    p = str(processo or "").strip()
    m = RX_EMEC.match(p) or RX_SEI.search(p)
    if m:
        return pd.Timestamp(int(m.group(1)), 1, 1)
    return None


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
def compilar(parquet_dou, parquet_seres, parquet_inep=None, log=print):
    df = pd.read_parquet(parquet_dou)
    log(f"[compilar] {len(df)} linhas do DOU")
    for c in ("texto_inicio", "processos_citados"):
        if c not in df.columns:
            raise SystemExit(f"[ERRO] parquet sem a coluna '{c}': foi gerado por uma versao "
                             "antiga do dou_extrair.py — rode a extracao de novo antes.")

    # LIMPEZA CRITICA: celula ausente vira NaN no parquet e, convertida para string,
    # vira o texto "nan" — que parece preenchido e fazia a propagacao PULAR a celula
    # (bug pego em auditoria: 94% "preenchido" no log, 1% de verdade no Excel).
    # No pandas 3 as colunas de texto vem com dtype "str" (nao "object") e o vazio e <NA>,
    # que convertido para string vira o TEXTO "<NA>" — mais um jeito de celula vazia se
    # passar por preenchida. A limpeza cobre os dois dtypes e os tres disfarcos.
    for c in df.columns:
        if df[c].dtype == object or str(df[c].dtype) in ("str", "string"):
            df[c] = (df[c].fillna("").astype(str)
                     .replace({"nan": "", "None": "", "<NA>": ""}))

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
    for c in seres.columns:
        if seres[c].dtype == object or str(seres[c].dtype) in ("str", "string"):
            seres[c] = (seres[c].fillna("").astype(str)
                        .replace({"nan": "", "None": "", "<NA>": ""}))

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
    # Terceira fonte de codigos: o cadastro de IES do Censo INEP (2.580 IES com codigo,
    # mantenedora e municipio oficiais) — e o que permite preencher cod_ies para IES que
    # nunca tiveram o codigo citado num ato do DOU.
    pares_ies, pares_mant = [], []
    if parquet_inep:
        inep = pd.read_parquet(parquet_inep)
        pares_ies = [(_norm(n), str(c)) for n, c in zip(inep["NO_IES"], inep["CO_IES"])]
        if "SG_IES" in inep:
            pares_ies += [(_norm(sg), str(c)) for sg, c in zip(inep["SG_IES"], inep["CO_IES"])
                          if isinstance(sg, str) and len(str(sg)) > 3]
        pares_mant = [(_norm(m), str(c)) for m, c in
                      zip(inep["NO_MANTENEDORA"], inep["CO_MANTENEDORA"])]
    m_cod_ies = _mapa_unico(
        [( _norm(i), c) for i, c in zip(df.get("ies", ""), df.get("cod_ies", "")) if str(c).strip()]
        + [(_norm(i), c) for i, c in zip(seres["ies"], seres["cod_ies"])]
        + pares_ies)
    m_cod_mant = _mapa_unico([(_norm(m), c) for m, c in
                              zip(seres["mantenedora"], seres["cod_mantenedora"])]
                             + pares_mant)
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
        (float(m.group(1)) if (m := RX_VAGAS_TXT.search(str(t))) else float("nan"))
        for t in df.loc[sem_vagas, "texto_inicio"]]
    log(f"[compilar] pos-preenchimento: cod_ies {df['cod_ies'].astype(str).str.strip().astype(bool).mean()*100:.0f}% | "
        f"municipio {df['municipio'].astype(str).str.strip().astype(bool).mean()*100:.0f}%")

    # ---------------- colunas finais dos atos do DOU
    df["orgao_resumido"] = df["orgao"].astype(str).str.split("/").str[-1].str.strip()
    df["data_pedido"] = df["processo_emec"].map(ano_do_pedido)
    df["data_decisao"] = df["_data"]          # datetime de verdade
    df["tipo_decisao"] = df["tipo_ato"]
    df["numero_vagas"] = df["vagas_num"]
    df["processo"] = df["processo_emec"]
    df["retificacao"] = df.get("retificacao", False).map(
        lambda x: "Sim (republicado com correcao)" if x else "Nao")
    if "resumo_texto" not in df:
        df["resumo_texto"] = ""
    vazio_resumo = ~df["resumo_texto"].astype(str).str.strip().astype(bool)
    df.loc[vazio_resumo, "resumo_texto"] = df.loc[vazio_resumo, "texto_inicio"].astype(str).str[:300]

    # ---------------- pendentes da SERES que nao tem DECISAO no DOU
    # Conferencia pelo proprio DOU: como toda decisao obrigatoriamente sai la, um processo
    # pendente so deixa a lista se apareceu num ato DECISORIO. Mencao em ato intermediario
    # (sobrestamento, cautelar, diligencia) nao e decisao — a linha pendente permanece.
    DECISORIOS = {"autorizacao", "reconhecimento", "renovacao_reconhecimento",
                  "credenciamento", "recredenciamento", "aditamento_aumento_vagas",
                  "reducao_vagas", "descredenciamento", "desativacao"}
    ja_no_dou = set(df.loc[df["tipo_decisao"].isin(DECISORIOS), "processo"].astype(str))
    pend = seres[~seres["ref_emec"].astype(str).isin(ja_no_dou)].copy()
    log(f"[compilar] pendentes SERES sem ato no DOU: {len(pend)} de {len(seres)}")
    pend_rows = pd.DataFrame({
        "data_pedido": pd.to_datetime(pend.get("data_protocolo", ""), format="%d/%m/%Y",
                                      errors="coerce"),
        "data_decisao": pd.NaT,               # sem decisao: fica vazio na coluna de data
        "tipo_decisao": "pendente: " + pend["situacao_mec"].astype(str),
        "uf": pend["uf"], "municipio": pend["municipio"],
        "mantenedora": pend["mantenedora"], "cod_mantenedora": pend["cod_mantenedora"],
        "ies": pend["ies"], "cod_ies": pend["cod_ies"],
        "curso": pend.get("curso", "MEDICINA").replace("", "MEDICINA"),
        "numero_vagas": None,
        "processo": pend["ref_emec"],
        "situacao_recurso": "nao se aplica (sem decisao)",
        "retificacao": "Nao",
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
    # (exceto as colunas de DATA, que precisam continuar tipadas como data no Excel;
    #  nelas o vazio significa "sem decisao"/"protocolo desconhecido" — explicado nas Notas)
    for c in [c for c in COLUNAS_FINAIS if c not in ("data_pedido", "data_decisao", "numero_vagas")]:
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
     "numero do processo (e-MEC comeca pelo ano: 2018xxxxx; SEI traz o ano apos a barra) e, "
     "para a coluna ser DATA no Excel, aparece como 01/01 do ano — dia e mes sao convencao. "
     "Data exata de protocolo so existe para os pendentes de Medicina (planilha SERES). "
     "Celula vazia = numero de processo que nao carrega o ano."],
    ["data_decisao", "Data de publicacao do ato no DOU (tipo data). VAZIA = processo ainda "
     "sem decisao (pendente da planilha SERES). Um pendente so sai da lista quando aparece "
     "em ato DECISORIO no DOU — mencao em sobrestamento/cautelar nao conta como decisao."],
    ["retificacao", "'Sim' = o ato foi republicado com correcao (o DOU marca com (*) no "
     "titulo). A linha mantida e sempre a versao mais recente (corrigida)."],
    ["tipo_decisao", "autorizacao, reconhecimento, renovacao, credenciamento, cautelar, "
     "sancionador etc. 'pendente: ...' = processo da planilha SERES ainda sem decisao."],
    ["situacao_recurso", "'ato de recurso' = a linha E um recurso (detectado por padrao "
     "textual juridico, excluindo 'recursos financeiros/humanos/orcamentarios'); 'decisao "
     "recorrida' = alguma linha de recurso aponta para este processo; a ultima coluna traz o "
     "processo recorrido ('mesmo processo' quando o recurso corre no proprio pedido)."],
    ["Preenchimento", "Codigos de IES/mantenedora tambem vem do cadastro oficial do Censo "
     "INEP 2023 (2.580 IES), casados por nome exato normalizado. Nada e inventado. "
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


def montar(parquet_dou, parquet_seres, saida, parquet_inep=None, log=print):
    corpo, seres = compilar(parquet_dou, parquet_seres, parquet_inep, log)
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
           sys.argv[3] if len(sys.argv) > 3 else "Regulacao_Cursos_2018-2026.xlsx",
           sys.argv[4] if len(sys.argv) > 4 else None)
