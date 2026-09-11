"""Correcao UNICA da base historica do DOU (Regulacao_Cursos*.xlsx), 10/09/2026.

Aplica na aba Atos as duas correcoes do extrator, SEM refazer o levantamento e SEM
perder o enriquecimento ja existente (codigos do INEP, data_pedido, situacao_recurso...):

  1. tipo_decisao — reclassificado pelo VERBO do Art. 1 (dou_extrair.classificar novo).
     Antes, "Indeferir o pedido de AUTORIZACAO do curso de Medicina" era contado como
     AUTORIZACAO: a base registrava REJEICAO como aprovacao.
  2. curso / vagas / IES / mantenedora / municipio / uf / codigos — lidos da PROSA do
     Art. 1 nas portarias de curso unico (dou_extrair.detalhes_da_prosa). Antes so as
     TABELAS eram lidas, entao esses atos entravam vazios — foi por isso que sumiram
     TODAS as autorizacoes de Medicina de 2022 em diante.

So preenche celula VAZIA (nunca sobrescreve dado que ja existia) e so casa ato com
UMA linha dos dois lados (ato de tabela nao corre risco de troca de linha).

Uso:  python corrigir_base_dou.py <arquivo.xlsx> [--aplicar]
"""
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
PARQUET_V2 = os.path.join(BASE, "dou_mec_do1_2018_2026_linhas_v2.parquet")
CAMPOS = {"curso": "curso", "vagas_num": "numero_vagas", "ies": "ies",
          "mantenedora": "mantenedora", "municipio": "municipio", "uf": "uf",
          "cod_ies": "cod_ies", "cod_mantenedora": "cod_mantenedora",
          "cod_curso": "cod_curso"}


def _vazio(v):
    return str(v).strip().lower() in ("", "nan", "none", "<na>", "nao consta na fonte",
                                      "não consta na fonte", "0")


# Gravada na aba Notas: a correcao roda UMA vez POR VERSAO. v3 (10/09/2026) somou a
# auditoria de verbos: extintos->desativacao, revogacao, sem_efeito, unificacao_mantidas,
# suspensao de chamada publica. Re-rodar sobre base v2 e idempotente (retipo por link +
# preenchimento so de celula vazia).
MARCA = "correcao_v5_aplicada"


def ja_aplicada(notas):
    """True se a planilha ja passou pela correcao (marca na aba Notas)."""
    if notas is None or "Assunto" not in getattr(notas, "columns", []):
        return False
    return (notas["Assunto"].astype(str).str.strip() == MARCA).any()


def linha_marca():
    from datetime import date
    return {"Assunto": MARCA,
            "Descricao": (f"{date.today().isoformat()} — base reclassificada pelo verbo do "
                          f"Art. 1 (indeferimento, extincao, revogacao, sem efeito, "
                          f"unificacao de mantidas) e campos lidos da prosa. "
                          f"NAO apagar: evita reprocessar.")}


def aplicar_em_df(atos, log=print):
    """Mesma correcao, sobre o DataFrame da aba Atos ja carregado (usada pelo robo)."""
    v2 = pd.read_parquet(PARQUET_V2)
    atos = _corrigir_df(v2, atos, log)
    return _suplementar(v2, atos, log)


def _nkey(v):
    """Normaliza um pedaco de chave: minusculo, SEM acento, sem '.0' de float
    (processo/vagas numericos no Excel viram '201912199.0' e desalinhavam TUDO)."""
    import unicodedata as u
    s = "" if v is None else str(v).strip().lower()
    if s in ("nan", "none", "<na>", "nao consta na fonte", "não consta na fonte"):
        return ""
    if s.endswith(".0") and s[:-2].replace(".", "").isdigit():
        s = s[:-2]
    return "".join(ch for ch in u.normalize("NFKD", s) if not u.combining(ch))


def _col(df, nome):
    return df[nome] if nome in df.columns else pd.Series([""] * len(df), index=df.index)


def _suplementar(v2, atos, log=print):
    """Devolve ao Excel as linhas-curso que a 1a carga PERDEU: o dedup antigo
    (ato+processo+curso+IES, sem municipio/vagas) colapsou o mesmo curso ofertado em
    municipios diferentes (polos EAD, despachos multi-campus). Duas travas contra
    duplicata: (1) so entra linha cuja chave completa nao exista; (2) TETO por trio
    (link,curso,ies) = n_parquet - n_excel — mesmo com chave desalinhada por
    enriquecimento (municipio preenchido depois), nunca adiciona alem do deficit real."""
    import dou_alerta
    from collections import Counter

    def trio(link, curso, ies):
        return _nkey(link) + "|" + _nkey(curso) + "|" + _nkey(ies)

    def chave6(link, proc, curso, ies, municipio, vagas):
        return "|".join((_nkey(link), _nkey(proc), _nkey(curso), _nkey(ies),
                         _nkey(municipio), _nkey(vagas)))

    tem6 = set(chave6(*t) for t in zip(
        atos["link"], _col(atos, "processo"), _col(atos, "curso"),
        _col(atos, "ies"), _col(atos, "municipio"), _col(atos, "numero_vagas")))
    n_xl = Counter(trio(l, c, i) for l, c, i in zip(
        atos["link"], _col(atos, "curso"), _col(atos, "ies")))

    links_xl = set(atos["link"].astype(str))
    cand = v2[v2["link"].astype(str).isin(links_xl)].copy()
    cu = cand["curso"]
    nomeado = ~(cu.isna() | cu.astype(str).str.strip().str.lower().isin(
        ["", "nan", "none", "<na>", "nao consta na fonte", "não consta na fonte"]))
    cand = cand[nomeado]
    n_pq = Counter(trio(l, c, i) for l, c, i in zip(
        cand["link"], cand["curso"], cand["ies"]))
    saldo = {t: n_pq[t] - n_xl.get(t, 0) for t in n_pq if n_pq[t] > n_xl.get(t, 0)}

    idx = []
    for i, l, p, c, ie, m, v in zip(cand.index, cand["link"], cand["processo_emec"],
                                    cand["curso"], cand["ies"], cand["municipio"],
                                    cand["vagas_num"]):
        t = trio(l, c, ie)
        if saldo.get(t, 0) > 0 and chave6(l, p, c, ie, m, v) not in tem6:
            idx.append(i)
            saldo[t] -= 1
    if not idx:
        log("[corrigir] suplemento: nenhuma linha perdida a devolver")
        return atos
    faltam = cand.loc[idx]
    sup = dou_alerta.para_formato_excel(faltam)
    sup["fonte_detalhe"] = "tabela do ato (linha recuperada na correcao v3)"
    for col in atos.columns:
        if col not in sup.columns:
            sup[col] = ""
    sup = sup[[c for c in atos.columns]]
    log(f"[corrigir] suplemento: +{len(sup)} linha(s)-curso devolvidas "
        f"({faltam['link'].nunique()} documentos)")
    return pd.concat([atos, sup], ignore_index=True)


def corrigir(caminho, aplicar=False, log=print):
    v2 = pd.read_parquet(PARQUET_V2)
    xl = pd.ExcelFile(caminho)
    atos = xl.parse("Atos")
    log(f"[corrigir] Atos: {len(atos)} linhas | parquet v2: {len(v2)} linhas")
    atos = _corrigir_df(v2, atos, log)
    atos = _suplementar(v2, atos, log)
    return _gravar(caminho, xl, atos, log) if aplicar else atos


def _corrigir_df(v2, atos, log=print):
    tipo_por_link = dict(zip(v2["link"].astype(str), v2["tipo_ato"].astype(str)))
    uma_linha_v2 = v2["link"].astype(str).value_counts()
    uma_linha_v2 = set(uma_linha_v2[uma_linha_v2 == 1].index)
    uma_linha_xl = atos["link"].astype(str).value_counts()
    uma_linha_xl = set(uma_linha_xl[uma_linha_xl == 1].index)
    v2_por_link = {str(r.link): r for r in v2.itertuples() if str(r.link) in uma_linha_v2}

    # colunas de codigo/vagas viram TEXTO antes da correcao: no Excel elas chegam como
    # float64 (colunas vazias) e escrever "8692" nelas explode com TypeError no pandas 3
    for col in set(CAMPOS.values()):
        if col in atos.columns:
            atos[col] = atos[col].map(
                lambda v: "" if _vazio(v) else
                (str(int(v)) if isinstance(v, float) and float(v).is_integer() else str(v)))
    mud_tipo, preench, campos = 0, 0, {}
    antes_tipo = atos["tipo_decisao"].astype(str).copy()
    for i, r in atos.iterrows():
        link = str(r["link"])
        novo = tipo_por_link.get(link)
        if novo and novo != str(r["tipo_decisao"]):
            atos.at[i, "tipo_decisao"] = novo
            mud_tipo += 1
        if link in uma_linha_v2 and link in uma_linha_xl and _vazio(r.get("curso")):
            fonte = v2_por_link[link]
            achou = False
            for col_v2, col_xl in CAMPOS.items():
                val = getattr(fonte, col_v2, None)
                if col_xl in atos.columns and not _vazio(val) and _vazio(r.get(col_xl)):
                    # tudo entra como TEXTO (a coluna virou str acima); float vira inteiro
                    if isinstance(val, float) and float(val).is_integer():
                        val = str(int(val))
                    atos.at[i, col_xl] = str(val).strip()
                    campos[col_xl] = campos.get(col_xl, 0) + 1
                    achou = True
            if achou:
                preench += 1
                if "fonte_detalhe" in atos.columns:
                    atos.at[i, "fonte_detalhe"] = "prosa do Art. 1 (corrigido)"
    log(f"[corrigir] tipo_decisao alterado: {mud_tipo} | linhas preenchidas: {preench}")
    log(f"[corrigir] campos preenchidos: {campos}")
    dif = pd.crosstab(antes_tipo[antes_tipo != atos["tipo_decisao"].astype(str)],
                      atos["tipo_decisao"].astype(str)[antes_tipo != atos["tipo_decisao"].astype(str)])
    if len(dif):
        log("\n[corrigir] de -> para:\n" + dif.to_string())
    c = atos["curso"].astype(str).str.upper()
    med = atos[c.str.contains(r"\bMEDICINA\b", regex=True, na=False)
               & ~c.str.contains("VETERIN", na=False)]
    ano = pd.to_datetime(atos.loc[med.index, "data_decisao"], errors="coerce").dt.year
    log("\n[corrigir] MEDICINA por ano x tipo (DEPOIS):")
    log(pd.crosstab(ano, med["tipo_decisao"]).to_string())

    return atos


def _gravar(caminho, xl, atos, log=print):
    c = atos["curso"].astype(str).str.upper()
    med_nova = atos[c.str.contains(r"\bMEDICINA\b", regex=True, na=False)
                    & ~c.str.contains("VETERIN", na=False)]
    outras = {n: xl.parse(n) for n in xl.sheet_names
              if n not in ("Atos", "Medicina", "Funil", "Graficos", "Graf_Dados")}
    notas = outras.get("Notas")
    if notas is not None and not ja_aplicada(notas):
        outras["Notas"] = pd.concat([notas, pd.DataFrame([linha_marca()])],
                                    ignore_index=True)
    with pd.ExcelWriter(caminho, engine="openpyxl", date_format="DD/MM/YYYY",
                        datetime_format="DD/MM/YYYY") as xw:
        atos.to_excel(xw, sheet_name="Atos", index=False)
        med_nova.to_excel(xw, sheet_name="Medicina", index=False)
        for n, d in outras.items():
            d.to_excel(xw, sheet_name=n, index=False)
        for aba in ["Atos", "Medicina"] + list(outras):
            w = xw.book[aba]
            w.freeze_panes = "A2"
            w.auto_filter.ref = w.dimensions
    log(f"\n[ok] base corrigida: {caminho} | Atos={len(atos)} Medicina={len(med_nova)}")
    return atos


if __name__ == "__main__":
    arq = sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx"
    corrigir(arq, aplicar="--aplicar" in sys.argv)
