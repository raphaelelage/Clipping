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


MARCA = "correcao_v2_aplicada"      # gravada na aba Notas: a correcao roda UMA vez


def ja_aplicada(notas):
    """True se a planilha ja passou pela correcao (marca na aba Notas)."""
    if notas is None or "Assunto" not in getattr(notas, "columns", []):
        return False
    return (notas["Assunto"].astype(str).str.strip() == MARCA).any()


def linha_marca():
    from datetime import date
    return {"Assunto": MARCA,
            "Descricao": (f"{date.today().isoformat()} — base reclassificada pelo verbo do "
                          f"Art. 1 e campos lidos da prosa (indeferimento deixou de ser "
                          f"contado como autorizacao). NAO apagar: evita reprocessar.")}


def aplicar_em_df(atos, log=print):
    """Mesma correcao, sobre o DataFrame da aba Atos ja carregado (usada pelo robo)."""
    return _corrigir_df(pd.read_parquet(PARQUET_V2), atos, log)


def corrigir(caminho, aplicar=False, log=print):
    v2 = pd.read_parquet(PARQUET_V2)
    xl = pd.ExcelFile(caminho)
    atos = xl.parse("Atos")
    log(f"[corrigir] Atos: {len(atos)} linhas | parquet v2: {len(v2)} linhas")
    atos = _corrigir_df(v2, atos, log)
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
