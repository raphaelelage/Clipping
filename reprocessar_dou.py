"""Reprocessa OFFLINE o levantamento historico do DOU (dou_mec_do1_2018_2026_linhas.parquet)
com o classificador e o parser de prosa corrigidos — SEM baixar nada do in.gov.br: o texto
integral (2.500 caracteres, com o Art. 1) ja esta salvo no proprio parquet.

Conserta 3 bugs medidos em 10/09/2026:
  1. INDEFERIMENTO contado como autorizacao ("Indeferir o pedido de AUTORIZACAO do curso"
     casava com a regra de autorizacao) — rejeicao virava aprovacao na base;
  2. "Fica autorizado/reconhecido o curso" nao casava com as regras (so as formas
     substantivadas casavam), entao o ato era classificado pelos 'considerandos';
  3. portaria de curso UNICO traz curso/vagas/IES/mantenedora/municipio na PROSA do
     Art. 1, e o extrator so lia TABELAS — 4.433 atos entraram vazios, incluindo TODAS
     as autorizacoes de Medicina de 2022-2026.

Uso:  python reprocessar_dou.py [--aplicar]
      sem --aplicar: so mostra o diagnostico (nao grava nada).
"""
import io
import os
import sys

import pandas as pd

import dou_extrair as dx

BASE = os.path.dirname(os.path.abspath(__file__))
PARQUET = os.path.join(BASE, "dou_mec_do1_2018_2026_linhas.parquet")
SAIDA = os.path.join(BASE, "dou_mec_do1_2018_2026_linhas_v2.parquet")

CAMPOS_PROSA = ("curso", "cod_curso", "vagas", "ies", "cod_ies",
                "mantenedora", "cod_mantenedora", "municipio", "uf")


def _vazio(v):
    s = str(v).strip().lower()
    return s in ("", "nan", "none", "<na>", "nao consta na fonte", "não consta na fonte")


def reprocessar(log=print):
    df = pd.read_parquet(PARQUET)
    log(f"[reprocessar] {len(df)} linhas do levantamento historico")
    tipo_antes = df["tipo_ato"].copy()

    novos_tipos, preenchidos, campos_add = [], 0, {}
    for i, r in df.iterrows():
        texto = str(r.get("texto_inicio") or "")
        novos_tipos.append(dx.classificar(str(r.get("ato") or ""), texto))
        # prosa SO onde a tabela nao trouxe o curso (nunca sobrescreve dado de tabela)
        if _vazio(r.get("curso")):
            det = dx.detalhes_da_prosa(texto)
            if det:
                achou = False
                for k, v in det.items():
                    if k in CAMPOS_PROSA and _vazio(r.get(k)):
                        df.at[i, k] = v
                        campos_add[k] = campos_add.get(k, 0) + 1
                        achou = True
                if achou:
                    preenchidos += 1
                    df.at[i, "fonte_detalhe"] = "prosa do Art. 1"
    df["tipo_ato"] = novos_tipos
    if "vagas" in df.columns:
        df["vagas_num"] = df["vagas"].map(dx._so_numero_vagas)

    mudou = (tipo_antes != df["tipo_ato"])
    log(f"\n[reprocessar] tipo_ato mudou em {int(mudou.sum())} atos:")
    if mudou.any():
        cmp_ = pd.crosstab(tipo_antes[mudou], df.loc[mudou, "tipo_ato"])
        log(cmp_.to_string())
    log(f"\n[reprocessar] prosa preencheu {preenchidos} atos | campos: {campos_add}")

    df["_ano"] = pd.to_datetime(df["data_publicacao"], errors="coerce",
                                dayfirst=True).dt.year
    c = df["curso"].astype(str).str.upper()
    med = df[c.str.contains(r"\bMEDICINA\b", regex=True, na=False)
             & ~c.str.contains("VETERIN", na=False)]
    log("\n[reprocessar] MEDICINA por ano x tipo (DEPOIS da correcao):")
    log(pd.crosstab(med["_ano"], med["tipo_ato"]).to_string())
    return df.drop(columns=["_ano"])


if __name__ == "__main__":
    novo = reprocessar()
    if "--aplicar" in sys.argv:
        novo.to_parquet(SAIDA, index=False)
        print(f"\n[ok] gravado: {SAIDA}")
    else:
        print("\n(diagnostico apenas — rode com --aplicar para gravar o parquet v2)")
