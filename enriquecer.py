"""Enriquecimento do Funil: recupera cod_ies, municipio/UF e vagas que o ato do DOU
nao trouxe explicitamente. Pedido do dono (11/09/2026): "varredura bem aprofundada,
lembrando as nossas regras de marcar de amarelo o que for cruzamento e nao inventar".

DUAS CAMADAS, com regras DIFERENTES de marcacao:

CAMADA 1 — o dado JA ESTA no ato, so precisa ser lido (NAO pinta de amarelo, porque a
fonte continua sendo o DOU):
    ies = "516"                      -> o proprio campo e o codigo (299 linhas)
    ies = "UNIV. FEDERAL DE MG(575)" -> codigo entre parenteses no fim (721 linhas)
    ies = "Faculdade X (cod. 4198)"  -> codigo citado no texto (68 linhas)

CAMADA 2 — CRUZAMENTO com base externa (PINTA de amarelo e declara a fonte):
    nome da IES            -> cod_ies       (so nomes INEQUIVOCOS no e-MEC)
    cod_ies + nome do curso-> cod_curso, municipio, uf, vagas (so par UNICO)

Regra de ouro (a mesma do cruzamento INEP): ambiguidade NUNCA vira preenchimento. Se o
nome casa com mais de uma IES, ou o par (IES, curso) aparece em mais de um curso, a
celula fica vazia para o dono preencher a mao.

Fontes: cursos_emec.parquet e ies_emec.parquet, derivados do CSV publico "Cursos de
Graduacao do Brasil" (dados abertos do MEC).
"""
import os
import re
import unicodedata

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CURSOS_EMEC = os.path.join(BASE, "cursos_emec.parquet")
IES_EMEC = os.path.join(BASE, "ies_emec.parquet")

VAGAS_FONTE_EMEC = ("e-MEC — vagas AUTORIZADAS do curso (total vigente no cadastro, "
                    "nao e o numero de um pedido)")

_RX_SO_NUMERO = re.compile(r"^\s*(\d{2,6})\s*$")
_RX_PARENTESE = re.compile(r"^(.*?)[\s(]*\(\s*(\d{2,6})\s*\)\s*$", re.S)
_RX_COD_TEXTO = re.compile(r"c[oó]d(?:igo)?\.?\s*(?:e-?MEC)?\s*n?[ºo°]?\s*:?\s*(\d{2,6})", re.I)


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).upper().split())


def _vazio(v):
    return str(v).strip().lower() in ("", "nan", "none", "<na>", "nao consta na fonte",
                                      "não consta na fonte", "<na>")


def codigo_no_campo(valor):
    """(codigo, nome_limpo) lidos do PROPRIO campo — nada e cruzado aqui.
    Devolve (None, valor) quando nao ha codigo embutido."""
    v = str(valor or "").strip()
    if not v:
        return None, v
    m = _RX_SO_NUMERO.match(v)
    if m:
        return m.group(1), ""                      # o campo inteiro era o codigo
    m = _RX_PARENTESE.match(v)
    if m and m.group(1).strip():
        return m.group(2), m.group(1).strip(" -–(")
    m = _RX_COD_TEXTO.search(v)
    if m:
        return m.group(1), v
    return None, v


# O DOU escreve "UNIVERSIDADE PAULISTA - UNIP" e o e-MEC so "UNIVERSIDADE PAULISTA":
# a sigla colada no fim fazia 672 linhas nao casarem. As variantes abaixo sao TODAS
# conservadoras — tiram um sufixo obvio e exigem IGUALDADE EXATA no fim. Nada de
# similaridade/fuzzy: "CENTRO UNIVERSITARIO UNICA" NAO pode casar com "... UNIC".
_RX_SIGLA_FIM = re.compile(r"^(.*?)\s+[-–]\s+[A-Z0-9.]{2,14}$")
_RX_PARENTESE_FIM = re.compile(r"\s*\([^)]*\)\s*$")


def variantes_nome(nome):
    """Grafias alternativas SEGURAS do nome da IES (todas casadas por igualdade)."""
    n = _norm(nome)
    v = {n}
    m = _RX_SIGLA_FIM.match(n)
    if m and len(m.group(1).strip()) > 8:
        v.add(m.group(1).strip())
    v.add(_RX_PARENTESE_FIM.sub("", n).strip())
    v.add(re.sub(r"[.,]", "", n).strip())
    return {x for x in v if len(x) > 8}


def _carregar(log=print):
    cursos = ies = None
    if os.path.exists(CURSOS_EMEC):
        cursos = pd.read_parquet(CURSOS_EMEC)
    if os.path.exists(IES_EMEC):
        ies = pd.read_parquet(IES_EMEC)
    if cursos is None or ies is None:
        log("[enriquecer] tabelas do e-MEC ausentes — enriquecimento pulado")
    return cursos, ies


def _mapa_cadastro(log=print):
    """2a fonte de cod_ies: cadastro_ies.parquet (consolidado dos censos INEP), que usa
    a chave propria de pedidos_compilar (minusculas, sem pontuacao)."""
    caminho = os.path.join(BASE, "cadastro_ies.parquet")
    if not os.path.exists(caminho):
        return {}, None
    try:
        import pedidos_compilar as pc
        cad = pd.read_parquet(caminho)
        return dict(zip(cad["chave"].astype(str), cad["cod_ies"])), pc._chave
    except Exception as e:
        log(f"[enriquecer] cadastro_ies indisponivel ({type(e).__name__})")
        return {}, None


def enriquecer(funil, pintar, log=print):
    """Preenche cod_ies / cod_curso / municipio / uf / vagas no DataFrame `funil`.
    `pintar` recebe (indice, coluna) das celulas da CAMADA 2 (cruzamento externo).
    Devolve dict com a contagem de cada origem, para a nota de cabecalho."""
    stat = {"campo": 0, "ies_nome": 0, "curso": 0, "municipio": 0, "uf": 0, "vagas": 0}

    # ---------------- CAMADA 1: codigo que ja estava no campo (sem amarelo) ---------
    for i in funil.index:
        if not _vazio(funil.at[i, "cod_ies"]):
            continue
        cod, nome = codigo_no_campo(funil.at[i, "ies"])
        if cod:
            funil.at[i, "cod_ies"] = cod
            funil.at[i, "ies"] = nome          # nome sem o "(575)" grudado
            stat["campo"] += 1
    if stat["campo"]:
        log(f"[enriquecer] camada 1 (codigo lido do proprio ato, sem amarelo): "
            f"{stat['campo']} cod_ies")

    cursos, ies_tab = _carregar(log)
    if cursos is None or ies_tab is None:
        return stat

    # ---------------- CAMADA 2: cruzamento com o e-MEC (amarelo) --------------------
    por_nome = dict(zip(ies_tab["nome_norm"], ies_tab["cod_ies"]))
    mapa_cad, chave_cad = _mapa_cadastro(log)
    for i in funil.index:
        if not _vazio(funil.at[i, "cod_ies"]):
            continue
        nome = funil.at[i, "ies"]
        if _vazio(nome):
            continue
        cod = None
        for v in variantes_nome(nome):          # 1a fonte: e-MEC
            if v in por_nome:
                cod = por_nome[v]
                break
        if cod is None and mapa_cad and chave_cad:   # 2a fonte: cadastro INEP
            for v in (chave_cad(nome), chave_cad(_RX_SIGLA_FIM.sub(r"\1", _norm(nome)))):
                if v and v in mapa_cad:
                    cod = mapa_cad[v]
                    break
        if cod is not None and pd.notna(cod):
            funil.at[i, "cod_ies"] = str(int(cod))
            pintar.append((i, "cod_ies"))
            _marca_fonte(funil, i, "cod_ies")
            stat["ies_nome"] += 1
    if stat["ies_nome"]:
        log(f"[enriquecer] camada 2: {stat['ies_nome']} cod_ies pelo NOME da IES "
            f"(e-MEC + cadastro INEP, so nomes inequivocos)")

    # (cod_ies + nome do curso) -> curso do e-MEC, so quando o par e UNICO
    cont = cursos["_k_ies_curso"].value_counts()
    unicos = set(cont[cont == 1].index)
    por_par = {k: r for k, r in zip(cursos["_k_ies_curso"], cursos.itertuples())
               if k in unicos}
    for i in funil.index:
        cod_ies = funil.at[i, "cod_ies"]
        if _vazio(cod_ies):
            continue
        falta = [c for c in ("cod_curso", "municipio", "uf", "vagas")
                 if _vazio(funil.at[i, c])]
        if not falta:
            continue
        alvo = _norm(funil.at[i, "curso_padrao"]) or _norm(funil.at[i, "curso"])
        hit = por_par.get(str(int(float(cod_ies))) + "|" + alvo) if alvo else None
        if hit is None:
            continue
        for col, val in (("cod_curso", hit.cod_curso), ("municipio", hit.municipio),
                         ("uf", hit.uf), ("vagas", hit.vagas)):
            if col in falta and not _vazio(val):
                funil.at[i, col] = str(int(val)) if col in ("cod_curso", "vagas") else val
                pintar.append((i, col))
                _marca_fonte(funil, i, col)
                stat["curso" if col == "cod_curso" else col] += 1
                if col == "vagas":
                    funil.at[i, "vagas_fonte"] = VAGAS_FONTE_EMEC
    log(f"[enriquecer] camada 2 por (IES + curso): cod_curso={stat['curso']} "
        f"municipio={stat['municipio']} uf={stat['uf']} vagas={stat['vagas']}")
    return stat


def _marca_fonte(funil, i, col):
    """Anota na coluna fonte_externa que `col` veio do e-MEC (auditoria por linha)."""
    atual = str(funil.at[i, "fonte_externa"] or "")
    if "e-MEC:" in atual:
        if col not in atual:
            funil.at[i, "fonte_externa"] = atual + ", " + col
    else:
        funil.at[i, "fonte_externa"] = (atual + " | " if atual else "") + "e-MEC: " + col
