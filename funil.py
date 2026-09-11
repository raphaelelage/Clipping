"""Funil de regulacao: deriva de Atos (log de eventos do DOU) o ESTADO ATUAL de cada curso.

Modelo (ver ARCHITECTURE.md):
  - trilho do curso (fases): 0 protocolado/sobrestado (so Medicina, via SERES) ->
    1 autorizado -> 2 reconhecido -> 3 renovacao de reconhecimento (ciclo) -> F desativado
  - eventos transversais NAO mudam a fase: vagas (aditamento/reducao), medida cautelar,
    sancionador/supervisao, sobrestamento — viram colunas/flags.
  - fase atual = ato do TRILHO com data mais recente (empate: fase maior). Universidades/
    centros universitarios criam curso sem autorizacao (autonomia): o primeiro ato pode
    ser direto o reconhecimento — por isso a fase e "a mais recente", nao "a sequencia".
  - `via`: Judicial (ref judicial em algum ato) > Chamamento Mais Medicos (tipo de ato)
    > Ordinaria. `regime_seres` guarda a portaria de regime dos pedidos pendentes.
  - `curso_padrao`: nome padronizado ("MEDICINA (Bacharelado)" -> "Medicina") p/ grafico.

CRUZAMENTO INEP (Censo da Educacao Superior, cursos_inep.parquet): celulas que o DOU nao
informa (vagas, cod_curso, cod_ies, curso, uf, municipio) sao completadas pelo Censo QUANDO
o cruzamento e inequivoco — por cod_curso, ou por (cod_ies + nome do curso) quando o par e
UNICO no Censo. Nada e inventado: toda celula preenchida assim sai PINTADA DE AMARELO no
Excel (regra do dono, 10/09/2026) e a coluna fonte_externa lista quais campos vieram de fora do DOU.

Fonte da verdade e o log Atos: este modulo NUNCA edita Atos, so (re)escreve a aba Funil.
Uso: python funil.py <arquivo.xlsx>   (ou funil.gerar(caminho) pelo robo)
"""
import os
import re
import sys
import unicodedata

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
INEP_PARQUET = os.path.join(BASE, "cursos_inep.parquet")
EMEC_PARQUET = os.path.join(BASE, "cursos_emec.parquet")
CAUTELARES_JSON = os.path.join(BASE, "cautelares_enamed_2026.json")
AMARELO = "FFF6C453"
VERDE_MANUAL = "FFE2EFDA"    # celula corrigida A MAO pelo dono (aba Ajustes)          # celula preenchida via INEP (nao consta no ato do DOU)

# status_regulatorio (dono, 10/09/2026): responde "quantos podem de fato entrar no
# mercado". Regras deterministas sobre campos ja existentes + a base oficial de
# cautelares do Enamed. O STATUS e curto; a REFERENCIA NORMATIVA vai em coluna propria
# (ref_regulatoria) — pedido do dono. "Sem via administrativa" substituiu o apelido
# "sem trilho": sao pedidos do Edital de Chamamento 1/2023, revogado pela Portaria MEC
# 129/2026 — e a ADC 81 firmou que chamamento e o UNICO caminho administrativo.
ST_SOBRESTADO = ("Sobrestado",
                 "ADC 81 (STF): sobrestado pela MC; merito julgado em 04/06/2024 "
                 "(chamamento publico e constitucional; embargos pendentes) — "
                 "planilha SERES, foto de 06/2024")
ST_JUDICIAL = ("Tramita por decisao judicial",
               "Portaria SERES 531/2023 (padrao decisorio p/ judicializados)")
ST_SEM_VIA = ("Sem via administrativa (edital revogado)",
              "Portaria MEC 129/2026 revogou o Edital de Chamamento 1/2023")
MUNICIPIOS_IBGE = os.path.join(BASE, "municipios_ibge.parquet")

FASE_TRILHO = {
    "autorizacao": (1, "1. Autorizado"),
    "reconhecimento": (2, "2. Reconhecido"),
    "renovacao_reconhecimento": (3, "3. Renovacao de reconhecimento"),
    # INDEFERIDO nao e etapa do trilho, e SAIDA: o pedido foi negado. Fica como fase
    # para o curso nao cair no balde "(sem ato do trilho)" e sumir da leitura — se
    # depois vier autorizacao, ela e mais recente e assume (a fase e sempre o ULTIMO ato).
    "indeferimento": (8, "F. Indeferido (pedido negado)"),
    "desativacao": (9, "F. Desativado"),
}
FASE_PENDENTE = {
    "pendente: em tramitacao": (0, "0. Protocolado (em tramitacao)"),
    "pendente: sobrestado (MC ADC 81)": (0, "0. Sobrestado (ADC 81)"),
}

# ordem das colunas da aba Funil — pensada para virar grafico (codigos primeiro)
COLS_FUNIL = ["cod_ies", "ies", "cod_curso", "curso_padrao", "curso", "uf", "municipio",
              "municipio_check", "fase_atual", "situacao_emec",
              "data_fase", "via",
              "status_regulatorio", "ref_regulatoria", "regime_seres", "vagas",
              "vagas_fonte", "sancionador", "qtd_atos", "ato_da_fase",
              "mantenedora", "processo_recente", "fonte_externa", "link_fonte"]

# o que o numero de VAGAS mede, conforme o ato de onde saiu (o cuidado do dono,
# 10/09/2026: aumento de vagas NAO e o total da IES; INEP e o total do curso existente)
_VAGAS_FONTE = {
    "autorizacao": "DOU — autorizacao (vagas do curso autorizado)",
    "aditamento_aumento_vagas": "DOU — aditamento de aumento de vagas (numero do ATO; "
                                "pode ser o total ja ampliado, nao so o acrescimo)",
    "reducao_vagas": "DOU — reducao de vagas",
    "reconhecimento": "DOU — reconhecimento (vagas informadas no ato)",
    "renovacao_reconhecimento": "DOU — renovacao de reconhecimento (vagas do ato)",
}
VAGAS_FONTE_INEP = "INEP Censo 2024 — vagas TOTAIS ofertadas do curso existente (nao e o pedido)"


_CAMPOS_AJUSTE = ("cod_ies", "cod_curso", "curso", "curso_padrao", "ies",
                  "mantenedora", "uf", "municipio", "vagas")


def _aplicar_ajustes(funil, xl, pintar_manual, log=print):
    """Aba AJUSTES (dono, 11/09/2026): o Funil e REGENERADO a cada rodada do robo,
    entao correcao feita direto nele evapora. O dono registra a correcao na aba
    Ajustes (link do ato + campo + valor; curso opcional para desambiguar ato com
    varios cursos) e ela e reaplicada AQUI em toda regeneracao — celula VERDE.
    Aplicada ANTES dos cruzamentos: o valor manual tem prioridade sobre tudo
    (nenhum preenchimento automatico sobrescreve celula ja preenchida)."""
    if "Ajustes" not in xl.sheet_names:
        return 0
    aj = xl.parse("Ajustes")
    aj.columns = [str(c).strip().lower() for c in aj.columns]
    if not {"link", "campo", "valor"}.issubset(aj.columns):
        log("[funil] aba Ajustes sem as colunas link/campo/valor — ignorada")
        return 0
    n = 0
    liga = funil["link_fonte"].astype(str).str.strip()
    for _, r in aj.iterrows():
        link = str(r.get("link") or "").strip()
        campo = str(r.get("campo") or "").strip().lower()
        valor = _limpa(r.get("valor"))
        if not (link and valor and campo in _CAMPOS_AJUSTE):
            continue
        sel = liga == link
        curso_f = _limpa(r.get("curso") if "curso" in aj.columns else "")
        if curso_f:
            sel = sel & funil["curso"].map(lambda c: _norm(curso_f) in _norm(c))
        for i in funil.index[sel]:
            funil.at[i, campo] = valor
            pintar_manual.append((i, campo))
            atual = str(funil.at[i, "fonte_externa"] or "")
            if "manual:" not in atual or campo not in atual:
                funil.at[i, "fonte_externa"] = ((atual + " | " if atual else "")
                                                + "manual: " + campo)                     if "manual:" not in atual else atual + ", " + campo
            n += 1
    if n:
        log(f"[funil] Ajustes manuais aplicados: {n} celula(s) (verde)")
    return n


def _padronizar_municipios(funil, log=print):
    """Nome do municipio -> grafia OFICIAL do IBGE, validada contra a UF da linha.
    municipio_check: 'ok' (casou na UF), 'nao encontrado na UF' (mantem o original,
    nada e inventado), 'sem UF para checar' ou 'sem municipio'."""
    if not os.path.exists(MUNICIPIOS_IBGE):
        log("[funil] municipios_ibge.parquet ausente — padronizacao pulada")
        funil["municipio_check"] = ""
        return funil
    ibge = pd.read_parquet(MUNICIPIOS_IBGE)
    oficial = {}
    for m, u in zip(ibge["municipio"], ibge["uf"]):
        oficial[(u, _norm(m))] = m
    novos, checks, corrigidos = [], [], 0
    RX_MUN_UF = re.compile(r"^(.*?)\s*/\s*([A-Za-z]{2})$")
    for m, u in zip(funil["municipio"], funil["uf"]):
        m0, u0 = _limpa(m), _limpa(u).upper()
        mm = RX_MUN_UF.match(m0)          # "Castanhal/PA" -> municipio + UF
        if mm:
            m0 = mm.group(1).strip()
            if not u0:
                u0 = mm.group(2).upper()
                funil.loc[funil["municipio"] == m, "uf"] = u0
        if not m0:
            novos.append(m0); checks.append("sem municipio"); continue
        if not u0:
            novos.append(m0); checks.append("sem UF para checar"); continue
        of = oficial.get((u0, _norm(m0)))
        if of is None:
            novos.append(m0); checks.append("nao encontrado na UF")
        else:
            if of != m0:
                corrigidos += 1
            novos.append(of); checks.append("ok")
    funil["municipio"] = novos
    funil["municipio_check"] = checks
    n_nok = sum(1 for c in checks if c == "nao encontrado na UF")
    log(f"[funil] municipios: {corrigidos} grafias padronizadas pelo IBGE; "
        f"{n_nok} nao encontrados na UF (mantidos como vieram)")
    return funil


def _carregar_emec(log=print):
    """cod_curso -> situacao no Cadastro e-MEC (Em atividade / Em extincao / Extinto).
    Fonte: CSV publico "Cursos de Graduacao do Brasil" (dados abertos do MEC). E a UNICA
    fonte que diz se o curso ainda existe: o DOU publica a extincao sem nomear o curso
    (so processo + IES) e o Censo INEP so enxerga curso em atividade."""
    if not os.path.exists(EMEC_PARQUET):
        log("[funil] cursos_emec.parquet ausente — situacao e-MEC pulada")
        return {}
    e = pd.read_parquet(EMEC_PARQUET)
    out = {str(int(c)): s for c, s in zip(e["cod_curso"], e["situacao_emec"])
           if pd.notna(c) and str(s).strip()}
    log(f"[funil] e-MEC: situacao de {len(out)} cursos carregada")
    return out


def _carregar_cautelares(log=print):
    """cod_curso -> (status curto, referencia normativa com as medidas)."""
    if not os.path.exists(CAUTELARES_JSON):
        log("[funil] cautelares_enamed_2026.json ausente — status Enamed pulado")
        return {}
    import json
    d = json.load(open(CAUTELARES_JSON, encoding="utf-8"))
    out = {}
    for num, p in (d.get("portarias") or {}).items():
        ref = (f"Portaria SERES {num}/2026 (Enamed): "
               + (", ".join(p.get("medidas") or []) or "medidas cautelares"))
        for c in p.get("cursos") or []:
            cod = str(c.get("cod_curso") or "").strip()
            if cod:
                out[cod] = ("Restrito - Enamed", ref)
    log(f"[funil] cautelares Enamed: {len(out)} cursos (Portarias SERES 72-76/2026)")
    return out


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _limpa(s):
    v = str(s).strip()
    return "" if v.lower() in ("nan", "none", "<na>", "-", "–", "nao consta na fonte",
                               "não consta na fonte", "nao se aplica", "não se aplica") \
        else v


_SEM_REF = {"NAO CONSTA NA FONTE", "NAO SE APLICA", ""}


def _tem_ref_judicial(serie):
    return serie.map(lambda v: _norm(v) not in _SEM_REF).any()


_PARENS_RX = re.compile(r"\s*\([^)]*\)\s*$")
_MINUS = {"de", "da", "do", "das", "dos", "e", "em", "a", "o", "para", "com"}


_RABO_IES_RX = re.compile(
    r"\s+(?:d[aeo]s?\s+)?(?:universidade|faculdade|centro\s+universit|escola|"
    r"instituto|fundacao|uni[a-z]*\b).*$", re.I)


def curso_padrao(nome):
    """"MEDICINA (Bacharelado)" -> "Medicina". Parentetico final cai; title-case com
    conectivos minusculos. Vazio/"nao consta" -> ""."""
    n = _limpa(nome)
    n = _PARENS_RX.sub("", n)
    # "Medicina da Universidade Brasil" (prosa antiga engoliu a IES) -> "Medicina";
    # so corta se sobrar nome de verdade antes
    m = _RABO_IES_RX.search(n)
    if m and len(n[:m.start()].strip()) >= 4:
        n = n[:m.start()]
    n = re.sub(r"\s+", " ", n).strip(" -–")
    if not n:
        return ""
    out = []
    for i, w in enumerate(n.split()):
        wl = w.lower()
        out.append(wl if (i > 0 and wl in _MINUS) else wl.capitalize())
    return " ".join(out)


def _int_ou_vazio(v):
    try:
        f = float(str(v).replace(",", "."))
        return str(int(f)) if f == f else ""          # NaN != NaN
    except Exception:
        return ""


_ORDEM_FASE = {"F. Desativado": 6, "F. Indeferido (pedido negado)": 5,
               "3. Renovacao de reconhecimento": 4, "2. Reconhecido": 3,
               "1. Autorizado": 2, "0. Sobrestado (ADC 81)": 1,
               "0. Protocolado (em tramitacao)": 1}


def _consolidar_por_cod(funil, pintar, pintar_manual, log=print):
    """O cod_curso pode chegar so no CRUZAMENTO (depois do agrupamento por chave), e o
    mesmo curso ficava em 2+ linhas (2.313 na auditoria de 11/09/2026: uma pela chave
    S/COD com grafia diferente de municipio/IES, outra pelo codigo). Aqui as linhas com
    o MESMO cod_curso viram uma so: vence a de fase mais avancada (empate: data mais
    recente, depois mais atos); qtd_atos soma; campo vazio do vencedor herda do outro;
    as celulas pintadas migram junto."""
    cod = funil["cod_curso"].astype(str).str.strip()
    dup = cod.ne("") & cod.ne("nan") & cod.duplicated(keep=False)
    if not dup.any():
        return funil, pintar, pintar_manual
    manter, remap, drop = {}, {}, set()
    dfd = funil[dup]
    rank = dfd["fase_atual"].map(lambda x: _ORDEM_FASE.get(str(x), 0))
    dtf = pd.to_datetime(dfd["data_fase"], errors="coerce")
    qa = pd.to_numeric(dfd["qtd_atos"], errors="coerce").fillna(0)
    ordem = pd.DataFrame({"c": cod[dup], "r": rank, "d": dtf, "q": qa},
                         index=dfd.index).sort_values(
        ["c", "r", "d", "q"], ascending=[True, False, False, False])
    for c, grupo in ordem.groupby("c", sort=False):
        idx = list(grupo.index)
        win, perde = idx[0], idx[1:]
        soma = int(pd.to_numeric(funil.loc[idx, "qtd_atos"], errors="coerce")
                   .fillna(0).sum())
        funil.at[win, "qtd_atos"] = soma
        if (funil.loc[idx, "via"] == "Judicial").any():
            funil.at[win, "via"] = "Judicial"
        fase_win = str(funil.at[win, "fase_atual"])
        for i in perde:
            for col in funil.columns:
                if col in ("qtd_atos", "via"):
                    continue
                # status/ref sao ESTADO ATUAL do pedido pendente: curso ja DECIDIDO
                # nao herda ("Sem via administrativa" num autorizado e contradicao —
                # bug pego na auditoria de 11/09/2026). regime_seres herda: e a
                # historia de como o pedido tramitou, continua verdadeira.
                if col in ("status_regulatorio", "ref_regulatoria") \
                        and not fase_win.startswith("0."):
                    continue
                if _limpa(funil.at[win, col]) == "" and _limpa(funil.at[i, col]) != "":
                    funil.at[win, col] = funil.at[i, col]
                    remap[(i, col)] = (win, col)
            drop.add(i)
    pintar = [remap.get(t, t) for t in pintar if t[0] not in drop or t in remap]
    pintar_manual = [remap.get(t, t) for t in pintar_manual
                     if t[0] not in drop or t in remap]
    n = len(drop)
    funil = funil.drop(index=drop).reset_index(drop=True)
    # os indices mudaram com o reset: reconstroi o mapa posicional
    novo_idx = {old: new for new, old in enumerate(
        [i for i in range(len(funil) + n) if i not in drop])}
    pintar = [(novo_idx[i], c) for i, c in pintar if i in novo_idx]
    pintar_manual = [(novo_idx[i], c) for i, c in pintar_manual if i in novo_idx]
    log(f"[funil] consolidacao por cod_curso: {n} linha(s) duplicada(s) fundida(s)")
    return funil, pintar, pintar_manual


def _carregar_inep(log=print):
    """cursos_inep.parquet -> (por_codigo, por_ies_nome). por_ies_nome so guarda pares
    (cod_ies, nome) UNICOS no Censo — ambiguidade nunca vira preenchimento."""
    if not os.path.exists(INEP_PARQUET):
        log("[funil] cursos_inep.parquet ausente — cruzamento INEP pulado")
        return {}, {}, {}
    inep = pd.read_parquet(INEP_PARQUET)
    inep["cod_curso"] = inep["cod_curso"].map(_int_ou_vazio)
    inep["cod_ies"] = inep["cod_ies"].map(_int_ou_vazio)
    por_codigo = {r.cod_curso: r for r in inep.itertuples() if r.cod_curso}
    # nome PADRONIZADO dos dois lados: o DOU escreve "DIREITO (BACHARELADO)",
    # o INEP "Direito" — sem normalizar, o par (IES, nome) nunca casava
    chave = inep["cod_ies"] + "|" + inep["curso"].map(lambda n: _norm(curso_padrao(n)))
    unicos = chave.value_counts()
    unicos = set(unicos[unicos == 1].index)
    por_ies_nome = {k: r for k, r in zip(chave, inep.itertuples())
                    if k in unicos and r.cod_ies}
    # desempate para IES multi-campus: (IES, nome, municipio) unico no Censo
    chave3 = chave + "|" + inep["municipio"].map(_norm)
    unicos3 = chave3.value_counts()
    unicos3 = set(unicos3[unicos3 == 1].index)
    por_ies_nome_mun = {k: r for k, r in zip(chave3, inep.itertuples())
                        if k in unicos3 and r.cod_ies}
    log(f"[funil] INEP: {len(por_codigo)} cursos por codigo, "
        f"{len(por_ies_nome)} pares (IES, nome) e "
        f"{len(por_ies_nome_mun)} trios (IES, nome, municipio) inequivocos")
    return por_codigo, por_ies_nome, por_ies_nome_mun


def gerar(caminho, log=print):
    xl = pd.ExcelFile(caminho)
    atos = xl.parse("Atos")
    seres = xl.parse("Medicina_SERES") if "Medicina_SERES" in xl.sheet_names \
        else pd.DataFrame()
    for c in atos.columns:
        if atos[c].dtype == object or str(atos[c].dtype) in ("str", "string"):
            atos[c] = atos[c].map(_limpa)
    # ato RETIFICADO '(*)': quando a republicacao esta na base, a versao ORIGINAL
    # e superada — sai das contagens (29 pares em 11/09/2026 dobravam qtd_atos e os
    # graficos por ano). A aba Atos continua com as duas, como historico.
    _tit = atos["ato"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    _bases_ret = set(_tit[_tit.str.contains(r"\(\*\)\s*$", regex=True)]
                     .str.replace(r"\s*\(\*\)\s*$", "", regex=True))
    _superado = _tit.isin(_bases_ret) & ~_tit.str.contains(r"\(\*\)\s*$", regex=True)
    if _superado.any():
        log(f"[funil] {int(_superado.sum())} linha(s) de versao ORIGINAL superada por "
            f"retificacao (*) fora das contagens")
        atos = atos[~_superado]
    atos["_data"] = pd.to_datetime(atos["data_decisao"], errors="coerce")
    atos.loc[atos["_data"].isna(), "_data"] = pd.to_datetime(
        atos.loc[atos["_data"].isna(), "data_pedido"], errors="coerce")

    # regime dos pedidos pendentes (SERES): pendente ainda NAO tem cod_curso —
    # a amarra e o processo e-MEC (ref_emec no SERES = processo na linha do funil)
    regime_por_proc = {}
    if len(seres):
        for ref, reg in zip(seres.get("ref_emec", "").map(_limpa),
                            seres.get("regime_juridico", "")):
            if ref and _limpa(reg):
                regime_por_proc[_norm(ref)] = _limpa(reg)

    atos["_cod"] = atos["cod_curso"].map(_int_ou_vazio)
    # "curso" que e SO NUMERO (5-8 digitos) e o CODIGO e-MEC que a tabela do ato
    # pos na coluna de nome (592/602 existem no cadastro — 11/09/2026). Vira
    # cod_curso; o NOME vem do cruzamento e-MEC/INEP (amarelo), nao fica numero.
    # 4 a 8 digitos (ha codigo e-MEC de 4 digitos: 9582, 6242...), MENOS o que
    # parece ANO (1900-2099) — "2019" como nome seria lixo, nao codigo
    so_numero = (atos["curso"].str.fullmatch(r"\d{4,8}").fillna(False)
                 & ~atos["curso"].str.fullmatch(r"(19|20)\d{2}").fillna(False))
    soh_sem_cod = so_numero & (atos["_cod"] == "")
    atos.loc[soh_sem_cod, "_cod"] = atos.loc[soh_sem_cod, "curso"]
    atos.loc[so_numero, "curso"] = ""   # numero NUNCA fica como nome de curso
    atos["_chave"] = atos["_cod"]
    vazio = atos["_chave"] == ""
    atos.loc[vazio, "_chave"] = ("S/COD|" + atos.loc[vazio, "ies"].map(_norm) + "|"
                                 + atos.loc[vazio, "curso"].map(_norm) + "|"
                                 + atos.loc[vazio, "municipio"].map(_norm))
    # ato so de IES (sem curso E sem codigo) nao entra no funil de cursos; ato com
    # cod_curso mas sem nome fica — o INEP preenche o nome depois
    atos = atos[(atos["curso"] != "") | (atos["_cod"] != "")]

    linhas = []
    for chave, g in atos.groupby("_chave", sort=False):
        g = g.sort_values("_data")
        ult = g.iloc[-1]
        trilho = g[g["tipo_decisao"].isin(FASE_TRILHO)]
        pend = g[g["tipo_decisao"].isin(FASE_PENDENTE)]
        if len(trilho):
            t = trilho.assign(_f=[FASE_TRILHO[x][0] for x in trilho["tipo_decisao"]])
            top = t.sort_values(["_data", "_f"]).iloc[-1]
            fase, data_fase, ato_fase = (FASE_TRILHO[top["tipo_decisao"]][1],
                                         top["_data"], top["ato"])
        elif len(pend):
            top = pend.iloc[-1]
            fase, data_fase, ato_fase = (FASE_PENDENTE[top["tipo_decisao"]][1],
                                         top["_data"], top["ato"])
            if not _limpa(ato_fase):   # pendente vem da planilha SERES, nao do DOU
                ato_fase = "Planilha oficial SERES (processos e-MEC em tramitacao)"
        else:
            top = ult
            fase, data_fase, ato_fase = ("(sem ato do trilho no periodo)",
                                         ult["_data"], ult["ato"])
        # link_fonte (dono, 11/09/2026): o DOU do ato que definiu a fase — e por onde
        # o dono confere/preenche a mao o que o scraper nao conseguiu extrair
        link_fase = _limpa(top.get("link", "")) or _limpa(ult.get("link", ""))

        cod = "" if chave.startswith("S/COD|") else chave
        judicial = _tem_ref_judicial(g["ref_judicial"])
        if fase.startswith("0. Sobrestado"):
            status, ref_reg = ST_SOBRESTADO
        elif fase.startswith("0. Protocolado"):
            status, ref_reg = ST_JUDICIAL if judicial else ST_SEM_VIA
        else:
            status, ref_reg = "", ""   # decididos: cautelar Enamed entra depois, por codigo
        chamamento = (g["tipo_decisao"] == "chamamento_mais_medicos").any() or \
                     g["ato"].map(lambda a: "MAIS MEDICOS" in _norm(a)).any()
        via = ("Judicial" if judicial
               else "Chamamento Mais Medicos" if chamamento else "Ordinaria")
        # vagas = ultimo ato que informou numero; guarda tambem QUE ato foi, p/ rotular
        vagas, vagas_fonte = "", ""
        for _, rr in g.iloc[::-1].iterrows():
            vv = _int_ou_vazio(rr["numero_vagas"])
            if vv:
                vagas = vv
                t = str(rr["tipo_decisao"])
                vagas_fonte = _VAGAS_FONTE.get(t, f"DOU — {t}")
                break
        sanc = g[g["tipo_decisao"] == "sancionador_supervisao"]
        nome_raw = ult["curso"]
        cnorm = _norm(nome_raw)
        linhas.append({
            "cod_ies": _int_ou_vazio(ult["cod_ies"]), "ies": ult["ies"],
            "cod_curso": cod, "curso_padrao": curso_padrao(nome_raw),
            "curso": nome_raw, "uf": ult["uf"], "municipio": ult["municipio"],
            "fase_atual": fase, "situacao_emec": "",
            "data_fase": data_fase.date() if pd.notna(data_fase) else None,
            "via": via, "status_regulatorio": status, "ref_regulatoria": ref_reg,
            "municipio_check": "", "vagas_fonte": vagas_fonte,
            "regime_seres": next((regime_por_proc[p] for p in
                                  g["processo"].map(lambda x: _norm(_limpa(x)))
                                  if p and p in regime_por_proc), ""),
            "vagas": vagas,
            "sancionador": (sanc.iloc[-1]["_data"].strftime("%d/%m/%Y")
                            if len(sanc) and pd.notna(sanc.iloc[-1]["_data"]) else ""),
            "qtd_atos": len(g), "ato_da_fase": ato_fase,
            "mantenedora": ult["mantenedora"], "processo_recente": ult["processo"],
            "fonte_externa": "", "link_fonte": link_fase,
        })
    funil = pd.DataFrame(linhas)
    # linha-LEGENDA de tabela (P.2.001/2023, stricto sensu): "Legenda:" foi parar em
    # ies E uf ao mesmo tempo — nao e curso, e rodape de tabela. Fora.
    _lixo = (funil["ies"].astype(str).str.strip().str.lower()
             == funil["uf"].astype(str).str.strip().str.lower()) & \
        (funil["uf"].astype(str).str.len() > 3)
    if _lixo.any():
        log(f"[funil] {int(_lixo.sum())} linha(s)-legenda de tabela descartada(s)")
        funil = funil[~_lixo].reset_index(drop=True)

    # ---------------- ajustes MANUAIS do dono (celulas verdes; prioridade maxima) ----
    pintar_manual = []
    _aplicar_ajustes(funil, xl, pintar_manual, log)

    # ---------------- cruzamento INEP (celulas amarelas; nada inventado) ------------
    por_codigo, por_ies_nome, por_ies_nome_mun = _carregar_inep(log)
    pintar = []                    # (indice_da_linha, coluna) preenchidos via INEP
    if por_codigo or por_ies_nome:
        for i in funil.index:
            r = funil.loc[i]
            hit = por_codigo.get(r["cod_curso"]) if r["cod_curso"] else None
            if hit is None and r["cod_ies"] and _norm(r["curso_padrao"]):
                k = r["cod_ies"] + "|" + _norm(r["curso_padrao"])
                hit = por_ies_nome.get(k)
                if hit is None and _norm(r["municipio"]):
                    hit = por_ies_nome_mun.get(k + "|" + _norm(r["municipio"]))
            if hit is None:
                continue
            preenchidos = []
            def _põe(col, valor):
                v = _limpa(valor)
                if v and not _limpa(r[col]):
                    funil.at[i, col] = v
                    pintar.append((i, col))
                    preenchidos.append(col)
            _põe("cod_curso", hit.cod_curso)
            _põe("cod_ies", hit.cod_ies)
            _põe("curso", hit.curso)
            _põe("uf", hit.uf)
            _põe("municipio", hit.municipio)
            _põe("vagas", _int_ou_vazio(hit.vagas))
            if "vagas" in preenchidos:      # veio do Censo: rotula como total do curso
                funil.at[i, "vagas_fonte"] = VAGAS_FONTE_INEP
            if not _limpa(r["curso_padrao"]) and _limpa(hit.curso):
                funil.at[i, "curso_padrao"] = curso_padrao(hit.curso)
                pintar.append((i, "curso_padrao"))
                preenchidos.append("curso_padrao")
            if preenchidos:
                funil.at[i, "fonte_externa"] = "INEP: " + ", ".join(preenchidos)
        log(f"[funil] INEP preencheu {len(pintar)} celulas em "
            f"{funil['fonte_externa'].ne('').sum()} cursos")

    # ------------- varredura profunda: cod_ies / cod_curso / municipio / uf / vagas --
    # (dono, 11/09/2026) Roda ANTES da situacao e-MEC para que os cod_curso recem
    # descobertos ja entrem no cruzamento seguinte. Camada 1 le o codigo que ja estava
    # no ato (sem amarelo); camada 2 cruza com o e-MEC e PINTA. Ver enriquecer.py.
    stat_enr = {}
    try:
        import enriquecer as _enr
        stat_enr = _enr.enriquecer(funil, pintar, log)
    except Exception as e:
        log(f"[funil] enriquecimento pulado ({type(e).__name__}: {e})")

    # ------------- situacao do curso pelo Cadastro e-MEC (amarelo: fonte externa) ----
    emec = _carregar_emec(log)
    n_emec = 0
    if emec:
        for i in funil.index:
            cod = funil.at[i, "cod_curso"]
            sit = emec.get(cod) if cod else None
            if sit:
                funil.at[i, "situacao_emec"] = sit
                n_emec += 1
                # NAO entra em `pintar`: a coluna INTEIRA vem do e-MEC (nenhum valor dela
                # sai do DOU), entao quem fica amarelo e o CABECALHO — pintar as 22 mil
                # celulas diluiria o amarelo das outras colunas, que marca preenchimento
                # pontual. A nota de cabecalho declara a fonte do mesmo jeito.
        vc = funil["situacao_emec"].value_counts().to_dict()
        log(f"[funil] e-MEC preencheu situacao em {n_emec} cursos: {vc}")

    # cautelares do Enamed: cruzamento OFICIAL por cod_curso (Portarias SERES 72-76/2026)
    cautelares = _carregar_cautelares(log)
    if cautelares:
        alvo = (funil["status_regulatorio"] == "") & funil["cod_curso"].isin(cautelares)
        funil.loc[alvo, "status_regulatorio"] = \
            funil.loc[alvo, "cod_curso"].map(lambda c: cautelares[c][0])
        funil.loc[alvo, "ref_regulatoria"] = \
            funil.loc[alvo, "cod_curso"].map(lambda c: cautelares[c][1])
        log(f"[funil] Enamed: {int(alvo.sum())} cursos decididos marcados como restritos")

    funil = _padronizar_municipios(funil, log)
    funil, pintar, pintar_manual = _consolidar_por_cod(funil, pintar, pintar_manual, log)

    # Medicina continua NO TOPO da aba, so que sem coluna dedicada (dono,
    # 11/09/2026: redundante — filtre curso_padrao = "Medicina")
    _med_topo = funil["curso"].map(
        lambda c: bool(re.search(r"\bMEDICINA\b", _norm(c)))
        and "VETERIN" not in _norm(c))
    for _c in ("cod_ies", "cod_curso", "vagas", "qtd_atos"):
        funil[_c] = pd.to_numeric(funil[_c], errors="coerce").astype("Int64")
    funil = (funil.assign(_m=_med_topo)
             .sort_values(["_m", "fase_atual", "data_fase"],
                          ascending=[False, True, False])
             .drop(columns="_m"))[COLS_FUNIL]

    # ---------------- grava: so a aba Funil muda; amarelo nas celulas do INEP -------
    # NOTA no cabecalho (linha 1): de onde veio TODO dado que nao estava no ato do DOU
    # (regra do dono, 10/09/2026 — sempre no cabecalho). Celula amarela = preenchida de
    # fonte externa; a coluna fonte_externa diz o que veio de fora do DOU em cada linha.
    n_amarelas = len(pintar)
    n_ibge = int((funil["municipio_check"] == "ok").sum())
    NOTA = (
        "NOTA DE FONTES — celulas AMARELAS foram preenchidas com dado que NAO consta no "
        "ato do DOU: "
        f"(1) INEP Censo da Educacao Superior 2024 (cod_curso, cod_ies, curso, uf, "
        f"municipio, vagas) — {n_amarelas} celulas, so em cruzamento inequivoco (por "
        "cod_curso, ou IES+nome unico); coluna fonte_externa detalha por linha. "
        f"(2) Municipios padronizados pela base oficial do IBGE e conferidos contra a UF "
        f"({n_ibge} 'ok' na coluna municipio_check). "
        f"(3) Cadastro e-MEC / dados abertos do MEC, arquivo \"Cursos de Graduacao do "
        f"Brasil\": a COLUNA situacao_emec inteira ({n_emec} cursos — Em atividade / Em "
        f"extincao / Extinto; por isso o CABECALHO dela e amarelo, nao cada celula; "
        f"o DOU publica a extincao sem nomear o curso) e o preenchimento de cod_ies pelo "
        f"NOME da IES ({stat_enr.get('ies_nome', 0)}) e de cod_curso/municipio/uf/vagas "
        f"pelo par (IES + curso) quando UNICO no cadastro "
        f"({stat_enr.get('curso', 0)}/{stat_enr.get('municipio', 0)}/"
        f"{stat_enr.get('uf', 0)}/{stat_enr.get('vagas', 0)}). "
        f"NAO amarelo (dado do proprio ato, so estava embutido no texto): "
        f"{stat_enr.get('campo', 0)} cod_ies lidos de \"NOME(codigo)\" ou do campo numerico. "
        "link_fonte (ultima coluna) leva ao ato no DOU para conferencia/preenchimento manual. "
        "(4) status_regulatorio/ref_regulatoria: ADC 81 (STF), Portaria MEC 129/2026 "
        "(revogacao do Edital de Chamamento 1/2023) e Portarias SERES 72-76/2026 (Enamed). "
        "ATENCAO vagas: veja a coluna vagas_fonte — vagas do INEP sao o TOTAL ofertado do "
        "curso EXISTENTE (nunca o numero de um pedido pendente nem o acrescimo de um "
        "aumento de vagas). CELULAS VERDES = correcao manual do dono via aba Ajustes (link do ato + campo + valor) - preencha LA, nunca direto no Funil: o Funil e regenerado pelo robo e edicoes diretas se perdem. Nada e estimado por IA.")

    abas = {n: xl.parse(n) for n in xl.sheet_names if n != "Funil"}
    if "Ajustes" not in abas:      # cria vazia com instrucao na 1a linha de dados
        abas["Ajustes"] = pd.DataFrame(
            [{"link": "(cole aqui o link_fonte da linha do Funil)",
              "curso": "(opcional: nome do curso p/ ato com varios)",
              "campo": "(um de: " + ", ".join(_CAMPOS_AJUSTE) + ")",
              "valor": "(o valor correto)"}])
    from openpyxl.styles import PatternFill, Font, Alignment
    fill = PatternFill(start_color=AMARELO, end_color=AMARELO, fill_type="solid")
    # nota na linha 1, cabecalho na 2, dados da 3 em diante
    pos = {i: k + 3 for k, i in enumerate(funil.index)}
    col_x = {c: j + 1 for j, c in enumerate(COLS_FUNIL)}
    with pd.ExcelWriter(caminho, engine="openpyxl",
                        date_format="DD/MM/YYYY", datetime_format="DD/MM/YYYY") as xw:
        for n, df in abas.items():
            df.to_excel(xw, sheet_name=n, index=False)
        funil.to_excel(xw, sheet_name="Funil", index=False, startrow=1)
        ws = xw.book["Funil"]
        ws.cell(row=1, column=1, value=NOTA)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLS_FUNIL))
        c = ws.cell(row=1, column=1)
        c.font = Font(italic=True, size=9, color="663300")
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.fill = PatternFill(start_color="FFFDF3D6", end_color="FFFDF3D6", fill_type="solid")
        ws.row_dimensions[1].height = 58
        # notas de celula nos cabecalhos (dono, 11/09/2026: Shift+F2 explicando
        # cada output de fase_atual e status_regulatorio)
        from openpyxl.comments import Comment
        _c_fase = Comment(
            "FASE ATUAL = ato mais recente do trilho regulatorio no DOU:\n"
            "0. Protocolado (em tramitacao) - pedido de Medicina ainda sem decisao "
            "(fonte: planilha oficial SERES, nao ha ato no DOU)\n"
            "0. Sobrestado (ADC 81) - pedido suspenso pela medida cautelar do STF\n"
            "1. Autorizado - curso autorizado a iniciar turmas\n"
            "2. Reconhecido - curso reconhecido (pode emitir diploma)\n"
            "3. Renovacao de reconhecimento - ciclo regular de revalidacao\n"
            "F. Indeferido (pedido negado) - pedido rejeitado pela SERES/MEC\n"
            "F. Desativado - curso extinto ou desativado\n"
            "(sem ato do trilho no periodo) - desde 2018 o curso so apareceu em atos "
            "transversais (vagas, supervisao, cautelar), nunca num ato de "
            "autorizacao/reconhecimento/renovacao", "Robo Clipping", 240, 420)
        ws.cell(row=2, column=col_x["fase_atual"]).comment = _c_fase
        _c_status = Comment(
            "STATUS REGULATORIO - so para pendentes de Medicina e cursos com "
            "restricao vigente:\n"
            "Sobrestado - MC na ADC 81 (STF): parado ate o transito em julgado\n"
            "Tramita por decisao judicial - anda por forca de liminar/sentenca "
            "(padrao decisorio da Portaria SERES 531/2023)\n"
            "Sem via administrativa (edital revogado) - pedido do Edital de "
            "Chamamento 1/2023, revogado pela Portaria MEC 129/2026: hoje nao ha "
            "caminho administrativo\n"
            "Restrito - Enamed - curso EXISTENTE sob medidas cautelares das "
            "Portarias SERES 72-76/2026 (reducao/suspensao de ingressos)\n"
            "(vazio) - curso decidido, sem restricao vigente conhecida",
            "Robo Clipping", 220, 400)
        ws.cell(row=2, column=col_x["status_regulatorio"]).comment = _c_status
        _notas_cols = {
            "sancionador": ("Data do ULTIMO ato sancionador/supervisao do MEC contra "
                            "este curso (processo administrativo por irregularidade). "
                            "Poucas linhas tem: a maioria dos atos de supervisao mira a "
                            "INSTITUICAO, sem nomear curso — esses ficam so na aba Atos. "
                            "Curso com historico aqui = risco maior de restricao futura."),
            "qtd_atos": ("Quantas linhas da aba Atos pertencem a este curso (a historia "
                         "dele no DOU desde 2018). A soma da coluna e MENOR que o total "
                         "da aba Atos de proposito: atos sem nome nem codigo de curso "
                         "(extincoes que so citam processo, credenciamento de IES, "
                         "CEBAS, sancionador de instituicao) nao viram linha aqui. Ato retificado (*) "
                         "conta UMA vez: a versao original superada fica fora."),
            "link_fonte": ("Link da FONTE da fase atual. Ato no DOU: in.gov.br. Linhas "
                           "PENDENTES (fase 0.x) apontam para a pagina da SERES/MEC: "
                           "pedido pendente NAO tem ato no DOU — a fonte e a planilha "
                           "oficial de processos em tramitacao."),
            "municipio_check": ("ok = grafia casou com o IBGE dentro da UF (padronizada); "
                                "nao encontrado na UF = mantido como veio (conferir); "
                                "sem municipio / sem UF para checar."),
            "situacao_emec": ("Situacao no Cadastro e-MEC (coluna INTEIRA de fonte "
                              "externa — por isso o cabecalho amarelo): Em atividade / "
                              "Em extincao / Extinto. E o unico lugar que diz se o curso "
                              "ainda existe: o DOU publica extincao sem nomear o curso."),
            "data_fase": "Data de publicacao do ato que definiu a fase atual.",
            "via": ("Judicial = algum ato do curso cita decisao judicial/liminar; "
                    "Chamamento Mais Medicos = veio de edital; senao Ordinaria."),
            "regime_seres": ("So para pendentes de Medicina: norma que rege a tramitacao "
                             "segundo a planilha da SERES (ex.: Portaria 531 = padrao "
                             "decisorio dos judicializados)."),
            "vagas_fonte": ("De ONDE saiu o numero de vagas (tipo de ato do DOU, INEP ou "
                            "e-MEC). ATENCAO: INEP/e-MEC = total do curso EXISTENTE, "
                            "nunca o numero de um pedido."),
            "fonte_externa": ("Auditoria por linha: quais campos vieram de FORA do DOU "
                              "(INEP:..., e-MEC: ..., manual: ...). Celula amarela = "
                              "cruzamento; verde = aba Ajustes (dono)."),
        }
        for _col, _txt in _notas_cols.items():
            if _col in col_x:
                ws.cell(row=2, column=col_x[_col]).comment = \
                    Comment(_txt, "Robo Clipping", 170, 380)
        fill_manual = PatternFill(start_color=VERDE_MANUAL, end_color=VERDE_MANUAL,
                                  fill_type="solid")
        for i, col in pintar_manual:
            if col in col_x and i in pos:
                ws.cell(row=pos[i], column=col_x[col]).fill = fill_manual
        for i, col in pintar:
            ws.cell(row=pos[i], column=col_x[col]).fill = fill
        # cabecalho amarelo = COLUNA INTEIRA de fonte externa (nao ha valor do DOU nela)
        for col in ("situacao_emec",):
            if col in col_x:
                ws.cell(row=2, column=col_x[col]).fill = fill
        ws.freeze_panes = "A3"
        ws.auto_filter.ref = "A2:" + ws.cell(row=2, column=len(COLS_FUNIL)).coordinate \
            + str(2 + len(funil))
        for aba in abas:
            w = xw.book[aba]
            w.freeze_panes = "A2"
            w.auto_filter.ref = w.dimensions
        # DATA SEM HORARIO em TODAS as abas (dono, 11/09/2026): a coluna chega do Excel
        # como datetime e o formato herdado mostrava "00:00:00" junto. Aqui a celula que
        # e data/datetime recebe DD/MM/YYYY de forma explicita — o valor nao muda, so a
        # exibicao (e o horario 00:00 deixa de poluir a leitura).
        import datetime as _dt
        for aba in xw.book.sheetnames:
            w = xw.book[aba]
            for linha in w.iter_rows():
                for cel in linha:
                    if isinstance(cel.value, (_dt.datetime, _dt.date)):
                        cel.number_format = "DD/MM/YYYY"
    log(f"[funil] {len(funil)} cursos | " + " | ".join(
        f"{k}={v}" for k, v in funil["fase_atual"].value_counts().items()))
    return funil


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx")
