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
Excel (regra do dono, 10/09/2026) e a coluna fonte_inep lista quais campos vieram do Censo.

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
CAUTELARES_JSON = os.path.join(BASE, "cautelares_enamed_2026.json")
AMARELO = "FFF6C453"          # celula preenchida via INEP (nao consta no ato do DOU)

# status_regulatorio (dono, 10/09/2026): responde "quantos podem de fato entrar no
# mercado". Regras deterministas sobre campos ja existentes + a base oficial de
# cautelares do Enamed. O STATUS e curto; a REFERENCIA NORMATIVA vai em coluna propria
# (ref_regulatoria) — pedido do dono. "Sem via administrativa" substituiu o apelido
# "sem trilho": sao pedidos do Edital de Chamamento 1/2023, revogado pela Portaria MEC
# 129/2026 — e a ADC 81 firmou que chamamento e o UNICO caminho administrativo.
ST_SOBRESTADO = ("Sobrestado", "MC na ADC 81 (STF) — suspenso ate o transito em julgado")
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
              "municipio_check", "medicina", "fase_atual", "data_fase", "via",
              "status_regulatorio", "ref_regulatoria", "regime_seres", "vagas",
              "vagas_fonte", "cautelar", "sancionador", "qtd_atos", "ato_da_fase",
              "mantenedora", "processo_recente", "fonte_inep"]

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
    for m, u in zip(funil["municipio"], funil["uf"]):
        m0, u0 = _limpa(m), _limpa(u).upper()
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


def curso_padrao(nome):
    """"MEDICINA (Bacharelado)" -> "Medicina". Parentetico final cai; title-case com
    conectivos minusculos. Vazio/"nao consta" -> ""."""
    n = _limpa(nome)
    n = _PARENS_RX.sub("", n)
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
        else:
            fase, data_fase, ato_fase = ("(sem ato do trilho no periodo)",
                                         ult["_data"], ult["ato"])

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
        cautelar = g[g["tipo_decisao"] == "medida_cautelar"]
        sanc = g[g["tipo_decisao"] == "sancionador_supervisao"]
        nome_raw = ult["curso"]
        cnorm = _norm(nome_raw)
        linhas.append({
            "cod_ies": _int_ou_vazio(ult["cod_ies"]), "ies": ult["ies"],
            "cod_curso": cod, "curso_padrao": curso_padrao(nome_raw),
            "curso": nome_raw, "uf": ult["uf"], "municipio": ult["municipio"],
            "medicina": "Sim" if (re.search(r"\bMEDICINA\b", cnorm)
                                  and "VETERIN" not in cnorm) else "",
            "fase_atual": fase,
            "data_fase": data_fase.date() if pd.notna(data_fase) else None,
            "via": via, "status_regulatorio": status, "ref_regulatoria": ref_reg,
            "municipio_check": "", "vagas_fonte": vagas_fonte,
            "regime_seres": next((regime_por_proc[p] for p in
                                  g["processo"].map(lambda x: _norm(_limpa(x)))
                                  if p and p in regime_por_proc), ""),
            "vagas": vagas,
            "cautelar": (cautelar.iloc[-1]["_data"].strftime("%d/%m/%Y")
                         if len(cautelar) and pd.notna(cautelar.iloc[-1]["_data"]) else ""),
            "sancionador": (sanc.iloc[-1]["_data"].strftime("%d/%m/%Y")
                            if len(sanc) and pd.notna(sanc.iloc[-1]["_data"]) else ""),
            "qtd_atos": len(g), "ato_da_fase": ato_fase,
            "mantenedora": ult["mantenedora"], "processo_recente": ult["processo"],
            "fonte_inep": "",
        })
    funil = pd.DataFrame(linhas)

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
                funil.at[i, "fonte_inep"] = "INEP: " + ", ".join(preenchidos)
        log(f"[funil] INEP preencheu {len(pintar)} celulas em "
            f"{funil['fonte_inep'].ne('').sum()} cursos")

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

    funil = funil[COLS_FUNIL].sort_values(
        ["medicina", "fase_atual", "data_fase"], ascending=[False, True, False])

    # ---------------- grava: so a aba Funil muda; amarelo nas celulas do INEP -------
    # NOTA no cabecalho (linha 1): de onde veio TODO dado que nao estava no ato do DOU
    # (regra do dono, 10/09/2026 — sempre no cabecalho). Celula amarela = preenchida de
    # fonte externa; a coluna fonte_inep diz o que veio do Censo em cada linha.
    n_amarelas = len(pintar)
    n_ibge = int((funil["municipio_check"] == "ok").sum())
    NOTA = (
        "NOTA DE FONTES — celulas AMARELAS foram preenchidas com dado que NAO consta no "
        "ato do DOU: "
        f"(1) INEP Censo da Educacao Superior 2024 (cod_curso, cod_ies, curso, uf, "
        f"municipio, vagas) — {n_amarelas} celulas, so em cruzamento inequivoco (por "
        "cod_curso, ou IES+nome unico); coluna fonte_inep detalha por linha. "
        f"(2) Municipios padronizados pela base oficial do IBGE e conferidos contra a UF "
        f"({n_ibge} 'ok' na coluna municipio_check). "
        "(3) status_regulatorio/ref_regulatoria: ADC 81 (STF), Portaria MEC 129/2026 "
        "(revogacao do Edital de Chamamento 1/2023) e Portarias SERES 72-76/2026 (Enamed). "
        "ATENCAO vagas: veja a coluna vagas_fonte — vagas do INEP sao o TOTAL ofertado do "
        "curso EXISTENTE (nunca o numero de um pedido pendente nem o acrescimo de um "
        "aumento de vagas). Nada e estimado por IA.")

    abas = {n: xl.parse(n) for n in xl.sheet_names if n != "Funil"}
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
        for i, col in pintar:
            ws.cell(row=pos[i], column=col_x[col]).fill = fill
        ws.freeze_panes = "A3"
        ws.auto_filter.ref = "A2:" + ws.cell(row=2, column=len(COLS_FUNIL)).coordinate \
            + str(2 + len(funil))
        for aba in abas:
            w = xw.book[aba]
            w.freeze_panes = "A2"
            w.auto_filter.ref = w.dimensions
    log(f"[funil] {len(funil)} cursos | " + " | ".join(
        f"{k}={v}" for k, v in funil["fase_atual"].value_counts().items()))
    return funil


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx")
