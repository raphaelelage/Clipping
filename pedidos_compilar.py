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
    "data_pedido", "data_decisao", "tipo_decisao", "ato", "uf", "municipio",
    "mantenedora", "cod_mantenedora", "ies", "cod_ies", "cod_curso", "curso", "numero_vagas",
    "processo", "situacao_recurso", "ref_judicial", "orgao_resumido",
    "resumo_texto", "retificacao", "fonte_detalhe", "link", "recurso_ref_processo",
]

# ------------------------------------------------------------------ helpers
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).lower().strip()


# 9 digitos = formato atual (AAAA+5); 8 digitos = formato antigo (AAAA+4), ainda visto
# em renovacoes de pedidos protocolados ate ~2010 e decididos anos depois
RX_EMEC = re.compile(r"^(200\d|201\d|202\d)\d{4,5}$")

# Sufixos societarios que variam entre o DOU e o Censo sem mudar quem e a mantenedora
# ("EDUCACIONAL X LTDA" no DOU, "EDUCACIONAL X" no Censo). Removidos APENAS da chave de
# comparacao — o nome exibido na planilha continua o original.
_RX_SOCIETARIO = re.compile(
    r"\b(ltda|s[ /.]?a|eireli|epp|me|cia|s[ /.]?s|s[ /.]?c|sociedade simples|"
    r"sociedade civil)\b\.?", )
_RX_PONTUACAO = re.compile(r"[^\w ]")


def _chave(s):
    """Chave de casamento: sem acento, sem pontuacao, caixa unica, espacos colapsados."""
    return re.sub(r"\s+", " ", _RX_PONTUACAO.sub(" ", _norm(s))).strip()


def _chave_mant(s):
    return re.sub(r"\s+", " ", _RX_SOCIETARIO.sub(" ", _chave(s))).strip()
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
def compilar(parquet_dou, parquet_seres, inep_ies=(), inep_cursos=(), log=print):
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

    # ---------------- preenchimento pelos cadastros oficiais (Censo INEP 2018 + 2023)
    # Multi-ano de proposito: IES extinta antes de 2023 so existe no cadastro antigo.
    # Matching por nome EXATO normalizado, com dois fallbacks deterministicos:
    # "FACULDADE X - FX" tenta o trecho antes do " - " e depois a sigla. So preenche
    # quando o nome mapeia para UM UNICO codigo — ambiguidade nunca vira chute.
    pares_ies, pares_ies_uf, pares_ies_mun, pares_mant, pares_local = [], [], [], [], []
    ies_para_mant = {}          # CO_IES -> CO_MANTENEDORA (censo mais recente vence)
    ies_sede = {}               # CO_IES -> (municipio sede, UF) do censo mais recente
    cursos_cad = []
    for pq in sorted(inep_ies or []):        # ordem alfabetica = cronologica (2018..2023)
        inep = pd.read_parquet(pq)
        pares_ies += [(_chave(n), str(c)) for n, c in zip(inep["NO_IES"], inep["CO_IES"])]
        if "SG_IES" in inep:
            pares_ies += [(_chave(sg), str(c)) for sg, c in zip(inep["SG_IES"], inep["CO_IES"])
                          if isinstance(sg, str) and len(str(sg)) > 3]
        pares_mant += [(_chave_mant(m), str(c)) for m, c in
                       zip(inep["NO_MANTENEDORA"], inep["CO_MANTENEDORA"])]
        if "SG_UF_IES" in inep:
            pares_ies_uf += [((_chave(n), str(u)), str(c)) for n, u, c in
                             zip(inep["NO_IES"], inep["SG_UF_IES"], inep["CO_IES"])]
        if "NO_MUNICIPIO_IES" in inep:
            pares_ies_mun += [((_chave(n), _chave(mu)), str(c)) for n, mu, c in
                              zip(inep["NO_IES"], inep["NO_MUNICIPIO_IES"], inep["CO_IES"])]
        if "NO_MUNICIPIO_IES" in inep:
            pares_local += [(_chave(n), f"{mu}|{u}") for n, mu, u in
                            zip(inep["NO_IES"], inep["NO_MUNICIPIO_IES"], inep["SG_UF_IES"])]
        for ci, cm in zip(inep["CO_IES"], inep["CO_MANTENEDORA"]):
            ies_para_mant[str(ci)] = str(cm)     # ano mais novo sobrescreve o antigo
        if "NO_MUNICIPIO_IES" in inep:
            for ci, mu, u in zip(inep["CO_IES"], inep["NO_MUNICIPIO_IES"], inep["SG_UF_IES"]):
                ies_sede[str(ci)] = (str(mu), str(u))
    for pq in (inep_cursos or []):
        cursos_cad.append(pd.read_parquet(pq))

    m_cod_ies = _mapa_unico(
        [(_chave(i), c) for i, c in zip(df.get("ies", ""), df.get("cod_ies", "")) if str(c).strip()]
        + [(_chave(i), c) for i, c in zip(seres["ies"], seres["cod_ies"])]
        + pares_ies)
    # nome ambiguo no pais inteiro pode ser unico DENTRO da UF (ex.: duas "Faculdade Sao
    # Judas" em estados diferentes) — segundo mapa, chaveado por (nome, UF)
    m_cod_ies_uf = _mapa_unico(pares_ies_uf)
    m_cod_ies_mun = _mapa_unico(pares_ies_mun)   # homonimas nacionais, unicas no municipio
    m_cod_mant = _mapa_unico([(_chave_mant(m), c) for m, c in
                              zip(seres["mantenedora"], seres["cod_mantenedora"])]
                             + pares_mant)
    # municipio em CASCATA: primeiro o que o proprio DOU prova (municipio do CAMPUS),
    # so depois a sede do cadastro INEP. Misturar os dois num mapa so criava conflito
    # campus x sede, a IES virava "ambigua" e a propagacao morria (medido: 73% -> 58%).
    m_local_dou = _mapa_unico([(_chave(i), f"{mu}|{u}") for i, mu, u in
                               zip(df.get("ies", ""), df.get("municipio", ""), df.get("uf", ""))
                               if str(mu).strip() and str(u).strip()])
    m_local_inep = _mapa_unico(pares_local)
    m_local = {**m_local_inep, **m_local_dou}          # DOU ganha do INEP

    def _busca_ies(nome_original, uf, municipio=""):
        """Cascata: nome exato -> (nome, municipio) -> (nome, UF) -> quebra no ' - '.
        A quebra no hifen usa o nome ORIGINAL (na chave a pontuacao ja virou espaco)."""
        nome_n = _chave(nome_original)
        if not nome_n:
            return ""
        if nome_n in m_cod_ies:
            return m_cod_ies[nome_n]
        mu = _chave(municipio)
        if mu and (nome_n, mu) in m_cod_ies_mun:
            return m_cod_ies_mun[(nome_n, mu)]
        if uf and (nome_n, uf) in m_cod_ies_uf:
            return m_cod_ies_uf[(nome_n, uf)]
        if " - " in str(nome_original):
            antes, _, depois = str(nome_original).partition(" - ")
            for pedaco in (antes, depois):
                k = _chave(pedaco)
                if k in m_cod_ies:
                    return m_cod_ies[k]
                if uf and (k, uf) in m_cod_ies_uf:
                    return m_cod_ies_uf[(k, uf)]
        return ""

    # Dois padroes vindos das proprias tabelas do DOU, com codigo exato (sem casamento):
    #   "UNIVERSIDADE FEDERAL DO PARANA(571)"  -> codigo entre parenteses no nome
    #   "516"                                  -> a celula da IES E o proprio codigo
    ies_nome_oficial = {}
    for pq in sorted(inep_ies or []):
        inep2 = pd.read_parquet(pq)
        for ci, nn in zip(inep2["CO_IES"], inep2["NO_IES"]):
            ies_nome_oficial[str(ci)] = str(nn)          # ano mais novo sobrescreve

    rx_cod_no_nome = re.compile(r"\((\d{2,6})\)\s*$")
    def _cod_embutido(r):
        cod = str(r.get("cod_ies") or "").strip()
        nome = str(r.get("ies") or "").strip()
        if cod:
            return cod, nome
        m = rx_cod_no_nome.search(nome)
        if m:                                            # nome com "(571)" no fim
            return m.group(1), rx_cod_no_nome.sub("", nome).strip()
        if nome.isdigit() and 2 <= len(nome) <= 6:       # celula que so tem o codigo
            return nome, ies_nome_oficial.get(nome, nome)
        return "", nome
    extraidos = df.apply(_cod_embutido, axis=1)
    df["cod_ies"] = [c for c, _ in extraidos]
    df["ies"] = [n for _, n in extraidos]

    df["_ies_n"] = df["ies"].map(_chave)
    df["cod_ies"] = df.apply(
        lambda r: r["cod_ies"] if str(r.get("cod_ies") or "").strip()
        else _busca_ies(r["ies"], str(r.get("uf") or "").strip(),
                        str(r.get("municipio") or "")), axis=1)

    # cod_mantenedora em CASCATA: 1) via cod_ies no censo (join exato por codigo, sem nome);
    # 2) pelo nome da mantenedora (chave sem sufixo societario)
    def _busca_mant(r):
        ci = str(r.get("cod_ies") or "").strip()
        if ci and ci in ies_para_mant:
            return ies_para_mant[ci]
        return m_cod_mant.get(_chave_mant(r.get("mantenedora")), "")
    df["cod_mantenedora"] = df.apply(_busca_mant, axis=1)
    faltava_local = ~(df["municipio"].astype(str).str.strip().astype(bool))
    df.loc[faltava_local, "municipio"] = [
        m_local.get(n, "|").split("|")[0] for n in df.loc[faltava_local, "_ies_n"]]
    faltava_uf = ~(df["uf"].astype(str).str.strip().astype(bool))
    df.loc[faltava_uf, "uf"] = [
        m_local.get(n, "|").split("|")[1] for n in df.loc[faltava_uf, "_ies_n"]]

    # o que AINDA ficou sem local ganha o municipio-SEDE da IES via cod_ies (join por
    # codigo no censo — exato). Quando o ato nao diz o endereco do campus, a sede e a
    # melhor informacao oficial disponivel; a aba Notas registra a convencao.
    def _sede(r, idx):
        v = str(r).strip()
        if v:
            return v
        return ies_sede.get(str(dfc), ("", ""))[idx]
    sem_mu = ~(df["municipio"].astype(str).str.strip().astype(bool))
    df.loc[sem_mu, "municipio"] = [ies_sede.get(str(c), ("", ""))[0]
                                   for c in df.loc[sem_mu, "cod_ies"]]
    sem_uf = ~(df["uf"].astype(str).str.strip().astype(bool))
    df.loc[sem_uf, "uf"] = [ies_sede.get(str(c), ("", ""))[1]
                            for c in df.loc[sem_uf, "cod_ies"]]

    # ------- cod_curso: (cod_ies + nome do curso) e, se ambiguo, + municipio.
    # O DOU escreve "HISTORIA (Licenciatura)" e o Censo so "Historia" — o grau entre
    # parenteses sai da chave de comparacao.
    def _curso_n(c):
        return _norm(re.sub(r"\(.*?\)", " ", str(c or "")))

    m_curso, m_curso_mun = {}, {}
    for cad in cursos_cad:
        for ci, nc, cc, mu in zip(cad["CO_IES"], cad["NO_CURSO"], cad["CO_CURSO"],
                                  cad.get("NO_MUNICIPIO", [""] * len(cad))):
            k = (str(ci), _curso_n(nc))
            m_curso.setdefault(k, set()).add(str(cc))
            m_curso_mun.setdefault(k + (_norm(mu),), set()).add(str(cc))
    m_curso = {k: v.pop() for k, v in m_curso.items() if len(v) == 1}
    m_curso_mun = {k: v.pop() for k, v in m_curso_mun.items() if len(v) == 1}

    def _busca_curso(r):
        ci = str(r.get("cod_ies") or "").strip()
        cn = _curso_n(r.get("curso"))
        if not ci or not cn:
            return ""
        return m_curso.get((ci, cn)) or m_curso_mun.get((ci, cn, _norm(r.get("municipio"))), "")

    df["cod_curso"] = df.apply(_busca_curso, axis=1)

    log(f"[compilar] pos-preenchimento: cod_ies {df['cod_ies'].astype(str).str.strip().astype(bool).mean()*100:.0f}% | "
        f"cod_curso {df['cod_curso'].astype(str).str.strip().astype(bool).mean()*100:.0f}% | "
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
        "ato": "",
        "uf": pend["uf"], "municipio": pend["municipio"],
        "mantenedora": pend["mantenedora"], "cod_mantenedora": pend["cod_mantenedora"],
        "ies": pend["ies"], "cod_ies": pend["cod_ies"],
        "cod_curso": pend.get("cod_curso", ""),
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
    # Colunas de codigo ficam VAZIAS quando nem o cadastro oficial resolve (a pedido:
    # nada de "nao consta na fonte" nelas); as demais levam o marcador explicito.
    SEM_MARCADOR = ("data_pedido", "data_decisao", "numero_vagas",
                    "cod_ies", "cod_mantenedora", "cod_curso")
    for c in [c for c in COLUNAS_FINAIS if c not in SEM_MARCADOR]:
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
    ["municipio/UF", "Prioridade: endereco do CAMPUS declarado no ato > municipio unico "
     "da IES na base > SEDE da IES no censo (via cod_ies). Quando o ato nao informa o "
     "endereco, o municipio mostrado e o da sede — pode diferir do campus do curso."],
    ["Pendentes", "Apenas Medicina tem lista publica de processos sem decisao (SERES, "
     "fotografia de 04/06/2024). Para os demais cursos o MEC nao publica lista equivalente; "
     "a consulta e caso a caso no e-MEC."],
    ["Cobertura", "DOU Secao 1, 2018 ate a data de corte, todos os dias uteis, zero falhas de "
     "coleta. Edicoes extras (DO1E) fora; retificacoes deduplicadas mantendo a mais recente "
     "(marcadas em fonte_detalhe)."],
]


def montar(parquet_dou, parquet_seres, saida, inep_ies=(), inep_cursos=(), log=print):
    corpo, seres = compilar(parquet_dou, parquet_seres, inep_ies, inep_cursos, log)
    corpo["_d"] = pd.to_datetime(corpo["data_decisao"], errors="coerce")
    corpo = corpo.sort_values("_d").drop(columns="_d")
    # valor de DATA pura na celula (sem 00:00:00 na barra de formulas do Excel)
    for c in ("data_pedido", "data_decisao"):
        corpo[c] = pd.to_datetime(corpo[c], errors="coerce").dt.date
    # \bMEDICINA\b com borda de palavra: "BIOMEDICINA" contem "MEDICINA" como substring
    # e entrava indevidamente no recorte (bug pego em teste com dado real)
    med = corpo[corpo["curso"].astype(str).str.contains(r"\bMEDICINA\b", case=False, regex=True)
                & ~corpo["curso"].astype(str).str.contains("VETERIN", case=False)]
    log(f"[montar] Atos={len(corpo)} | Medicina={len(med)}")
    notas = pd.DataFrame(NOTAS, columns=["Assunto", "Descricao"])
    with pd.ExcelWriter(saida, engine="openpyxl",
                        date_format="DD/MM/YYYY", datetime_format="DD/MM/YYYY") as xw:
        corpo.to_excel(xw, sheet_name="Atos", index=False)
        med.to_excel(xw, sheet_name="Medicina", index=False)
        seres.to_excel(xw, sheet_name="Medicina_SERES", index=False)
        notas.to_excel(xw, sheet_name="Notas", index=False)
        for aba in ("Atos", "Medicina", "Medicina_SERES"):
            ws = xw.book[aba]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            ws.column_dimensions["A"].width = 12   # data_pedido
            ws.column_dimensions["B"].width = 12   # data_decisao
    log(f"[ok] {saida}")


if __name__ == "__main__":
    # uso: pedidos_compilar.py <dou.parquet> <seres.parquet> <saida.xlsx> <dir_inep>
    # onde dir_inep contem inep_ies*.parquet e inep_cursos*.parquet (Censo 2018/2023)
    import glob as _glob
    dir_inep = sys.argv[4] if len(sys.argv) > 4 else ""
    montar(sys.argv[1], sys.argv[2],
           sys.argv[3] if len(sys.argv) > 3 else "Regulacao_Cursos_2018-2026.xlsx",
           inep_ies=sorted(_glob.glob(dir_inep + "/inep_ies*.parquet")) if dir_inep else (),
           inep_cursos=sorted(_glob.glob(dir_inep + "/inep_cursos*.parquet")) if dir_inep else ())
