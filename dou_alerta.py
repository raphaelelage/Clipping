"""Radar DOU: vigia diario dos atos do MEC que mexem com cursos — 100% deterministico.

O que faz a cada rodada do clipping (verticais educacao e saude_educacao):
  1. le a EDICAO DIARIA do DOU dos ultimos dias uteis (mesma tecnica do levantamento
     historico: leiturajornal -> JSON com todos os atos + orgao assinante);
  2. filtra os atos do MEC que tratam de regulacao de curso/IES e classifica o tipo;
  3. monta UMA FRASE POR DOCUMENTO ("autoriza o curso de MEDICINA da Faculdade X em
     Cidade/UF, 60 vagas") — Medicina sempre em primeiro;
  4. devolve tambem as linhas no MESMO formato do Excel historico (Regulacao_Cursos.xlsx
     do Drive), para o clipping.py anexar la.

Quem decide o que e alarme:
  - ALARME_SEMPRE: tipos raros e de alto impacto — qualquer curso dispara;
  - reconhecimento/renovacao: rotina em outros cursos (dezenas por semana, viraria spam),
    mas para MEDICINA tambem dispara.

Reusa dou_historico (busca da edicao) e dou_extrair (classificacao e tabelas), e o
cadastro_ies.parquet (consolidado dos censos INEP 2018-2023) para os codigos — assim o
robo no GitHub nao precisa baixar censo nenhum.
"""
import os
import re
from datetime import date, timedelta

# \bMEDICINA\b com borda de palavra: sem isso BIOMEDICINA vira falso MEDICINA.
RX_MEDICINA = re.compile(r"\bMEDICINA\b(?!\s+VETERIN)", re.I)

import pandas as pd

import dou_historico as dh
import dou_extrair as dx

ALARME_SEMPRE = {
    "autorizacao", "aditamento_aumento_vagas", "reducao_vagas", "credenciamento",
    "descredenciamento", "medida_cautelar", "sancionador_supervisao", "desativacao",
    "sobrestamento", "chamamento_mais_medicos",
}
ALARME_SO_MEDICINA = {"reconhecimento", "renovacao_reconhecimento"}

_VERBO = {
    "autorizacao": "autoriza",
    "aditamento_aumento_vagas": "aumenta as vagas de",
    "reducao_vagas": "reduz as vagas de",
    "reconhecimento": "reconhece",
    "renovacao_reconhecimento": "renova o reconhecimento de",
    "credenciamento": "credencia",
    "recredenciamento": "recredencia",
    "descredenciamento": "descredencia",
    "medida_cautelar": "impoe medida cautelar sobre",
    "sancionador_supervisao": "instaura/decide processo sancionador sobre",
    "desativacao": "desativa",
    "sobrestamento": "sobresta processo de",
    "chamamento_mais_medicos": "movimenta chamamento publico (Mais Medicos) de",
}


def _eh_medicina(curso):
    c = "" if curso is None or (isinstance(curso, float)) else str(curso)
    return bool(RX_MEDICINA.search(c)) and "VETERIN" not in c.upper()


def _cadastro():
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cadastro_ies.parquet")
    if not os.path.exists(caminho):
        return {}, {}, {}
    cad = pd.read_parquet(caminho)
    m_cod = dict(zip(cad["chave"], cad["cod_ies"]))
    m_mant = dict(zip(cad["cod_ies"], cad["cod_mantenedora"]))
    m_sede = {c: (mu, u) for c, mu, u in
              zip(cad["cod_ies"], cad["municipio_sede"], cad["uf_sede"])}
    return m_cod, m_mant, m_sede


def _dias_uteis_recentes(n):
    d, achados = date.today(), []
    while len(achados) < n:
        if d.weekday() < 5:
            achados.append(d)
        d -= timedelta(days=1)
    return achados


def coletar_novidades(dias=3, log=print):
    """Atos alarmantes dos ultimos `dias` dias uteis (hoje incluso).
    Devolve (frases, df_linhas): frases = [{"frase","link","medicina"}] uma por DOCUMENTO;
    df_linhas = linhas por curso no formato do Excel historico."""
    atos = []
    for d in _dias_uteis_recentes(dias):
        arr = dh.atos_do_dia(d, "do1")
        if arr is None:
            log(f"[radar] {d}: edicao inacessivel (sera reavaliada amanha)")
            continue
        for a in arr:
            if str(a.get("hierarchyStr", "")).startswith("Ministério da Educação"):
                a["_dia"] = d.isoformat()
                a["_secao"] = "do1"
                atos.append(a)
    if not atos:
        return [], pd.DataFrame()

    linhas = dx.extrair(atos, workers=6, log=lambda m: None)
    df = pd.DataFrame(linhas)
    if df.empty:
        return [], df
    alarme = df["tipo_ato"].isin(ALARME_SEMPRE) | (
        df["tipo_ato"].isin(ALARME_SO_MEDICINA) & df["curso"].map(_eh_medicina))
    df = df[alarme].copy()
    if df.empty:
        return [], df

    # codigos e sede pelo cadastro consolidado (sem depender de download de censo)
    m_cod, m_mant, m_sede = _cadastro()
    try:
        import pedidos_compilar as pc
        df["cod_ies"] = [str(c).strip() or m_cod.get(pc._chave(n), "")
                         for c, n in zip(df.get("cod_ies", ""), df["ies"])]
        df["cod_mantenedora"] = [m_mant.get(str(c), "") for c in df["cod_ies"]]
        sem_mu = ~df["municipio"].fillna("").astype(str).str.strip().astype(bool)
        df.loc[sem_mu, "municipio"] = [m_sede.get(str(c), ("", ""))[0]
                                       for c in df.loc[sem_mu, "cod_ies"]]
        sem_uf = ~df["uf"].fillna("").astype(str).str.strip().astype(bool)
        df.loc[sem_uf, "uf"] = [m_sede.get(str(c), ("", ""))[1]
                                for c in df.loc[sem_uf, "cod_ies"]]
    except Exception:
        pass

    frases = []
    for link, grupo in df.groupby("link", sort=False):
        g0 = grupo.iloc[0]
        tipo = g0["tipo_ato"]
        verbo = _VERBO.get(tipo, "publica ato sobre")
        meds = grupo[grupo["curso"].map(_eh_medicina)]
        tem_med = len(meds) > 0
        alvo = meds.iloc[0] if tem_med else g0
        def _limpo(v):
            return "" if v is None or pd.isna(v) or str(v).lower() in ("nan", "none")                    else str(v).strip()
        curso = _limpo(alvo.get("curso"))
        ies = _limpo(alvo.get("ies"))
        mu, uf = _limpo(alvo.get("municipio")), _limpo(alvo.get("uf"))
        vagas = alvo.get("vagas_num")
        pedacos = [f"{g0['ato']}:", verbo]
        if curso or ies:
            if curso:
                pedacos.append(f"o curso de {curso}")
            if ies:
                pedacos.append(f"da {ies}" if curso else ies)
            if mu:
                pedacos.append(f"em {mu}{'/' + uf if uf else ''}")
            if vagas is not None and pd.notna(vagas) and vagas:
                pedacos.append(f"({int(vagas)} vagas)")
        else:
            # ato sem tabela (sumulas do CNE etc.): a frase degrada para o proprio resumo
            resumo = _limpo(g0.get("resumo_texto")) or _limpo(str(g0.get("texto_inicio"))[:160])
            pedacos.append(f"— {resumo[:160]}")
        extras = len(grupo) - 1
        if extras > 0:
            pedacos.append(f"— e mais {extras} curso(s) no mesmo ato")
        frases.append({"frase": " ".join(pedacos), "link": link, "medicina": tem_med,
                       "tipo": tipo})
    frases.sort(key=lambda f: (not f["medicina"], f["tipo"]))
    log(f"[radar] {len(df)} linha(s) alarmante(s) em {df['link'].nunique()} documento(s)")
    return frases, df


def para_formato_excel(df):
    """Converte as linhas cruas para as colunas do Regulacao_Cursos.xlsx do Drive."""
    import pedidos_compilar as pc
    out = pd.DataFrame()
    out["data_pedido"] = df["processo_emec"].map(pc.ano_do_pedido)
    out["data_decisao"] = pd.to_datetime(df["data_publicacao"], format="%d/%m/%Y",
                                         errors="coerce")
    out["tipo_decisao"] = df["tipo_ato"]
    out["ato"] = df["ato"]
    out["uf"] = df.get("uf", "")
    out["municipio"] = df.get("municipio", "")
    out["mantenedora"] = df.get("mantenedora", "")
    out["cod_mantenedora"] = df.get("cod_mantenedora", "")
    out["ies"] = df.get("ies", "")
    out["cod_ies"] = df.get("cod_ies", "")
    out["cod_curso"] = ""
    out["curso"] = df.get("curso", "")
    out["numero_vagas"] = df.get("vagas_num")
    out["processo"] = df.get("processo_emec", "")
    out["situacao_recurso"] = "sem recurso identificado"
    out["ref_judicial"] = df.get("ref_judicial", "")
    out["orgao_resumido"] = df.get("orgao", "").astype(str).str.split("/").str[-1].str.strip()
    out["resumo_texto"] = df.get("resumo_texto", "").fillna("").astype(str)
    vazio = ~out["resumo_texto"].str.strip().astype(bool)
    if "texto_inicio" in df:
        out.loc[vazio, "resumo_texto"] = df.loc[vazio, "texto_inicio"].astype(str).str[:300]
    out["retificacao"] = df.get("retificacao", False).map(
        lambda x: "Sim (republicado com correcao)" if x else "Nao")
    out["fonte_detalhe"] = df.get("fonte_detalhe", "")
    out["link"] = df["link"]
    out["recurso_ref_processo"] = ""
    for c in out.columns:
        if out[c].dtype == object or str(out[c].dtype) in ("str", "string"):
            out[c] = out[c].fillna("").astype(str).replace({"nan": "", "None": "", "<NA>": ""})
    for c in ("data_pedido", "data_decisao"):
        out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
    return out
