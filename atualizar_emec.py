"""Regenera cursos_emec.parquet e ies_emec.parquet a partir do CSV publico do MEC —
100% deterministico, SEM IA (dono, 11/09/2026: nenhuma base pode depender de sessao).

PASSO MANUAL (1-2x/ano, ~1 min): baixar no SEU navegador (o portal exige CAPTCHA para
robo) o CSV "Cursos de Graduacao do Brasil" em
  https://dadosabertos.mec.gov.br/indicadores-sobre-ensino-superior/item/183-cursos-de-graduacao-do-brasil
e salvar na pasta do projeto. Depois:

  python atualizar_emec.py [caminho_do_csv]

Regras (as mesmas da 1a construcao, validadas em 11/09/2026):
  - 1 linha por cod_curso (EAD tem 1 linha por polo no CSV);
  - municipio/uf SO quando o curso tem UM unico municipio (polo ambiguo fica vazio —
    nada e inventado);
  - ies_emec.parquet guarda apenas nomes de IES INEQUIVOCOS (nome que aparece com 2+
    codigos fica fora — ambiguidade nunca vira preenchimento).
Depois de rodar: git add *.parquet && git commit (o robo usa os parquets do repo).
"""
import os
import sys
import unicodedata

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PADRAO = (r"C:\Users\Raphael\OneDrive\Documentos\1. Profissional\VS Code\Codigos"
              r"\Clipping New\PDA_Dados_Cursos_Graduacao_Brasil.csv")
COLS = ["CODIGO_CURSO", "CODIGO_IES", "NOME_IES", "NOME_CURSO", "MUNICIPIO", "UF",
        "QT_VAGAS_AUTORIZADAS", "MODALIDADE", "SITUACAO_CURSO",
        "CATEGORIA_ADMINISTRATIVA"]


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).upper().split())


def gerar(csv_path):
    df = pd.read_csv(csv_path, usecols=COLS, dtype=str, encoding="utf-8")
    print(f"[emec] CSV: {len(df):,} linhas, {df['CODIGO_CURSO'].nunique():,} cursos")

    # municipio so quando NAO ambiguo (curso EAD tem 1 linha por polo)
    nm = df.groupby("CODIGO_CURSO")["MUNICIPIO"].transform("nunique")
    df["_mun_ok"] = nm == 1
    cur = df.sort_values("CODIGO_CURSO").drop_duplicates("CODIGO_CURSO").copy()
    cur.loc[~cur["_mun_ok"], ["MUNICIPIO", "UF"]] = ""
    out = pd.DataFrame({
        "cod_curso": pd.to_numeric(cur["CODIGO_CURSO"], errors="coerce").astype("Int64"),
        "cod_ies": pd.to_numeric(cur["CODIGO_IES"], errors="coerce").astype("Int64"),
        "ies": cur["NOME_IES"].fillna(""),
        "curso": cur["NOME_CURSO"].fillna(""),
        "municipio": cur["MUNICIPIO"].fillna(""),
        "uf": cur["UF"].fillna(""),
        "vagas": pd.to_numeric(cur["QT_VAGAS_AUTORIZADAS"], errors="coerce").astype("Int64"),
        "modalidade": cur["MODALIDADE"].fillna(""),
        "situacao_emec": cur["SITUACAO_CURSO"].fillna(""),
        "categoria": cur["CATEGORIA_ADMINISTRATIVA"].fillna("")
        .str.replace("Pública ", "", regex=False),
    }).dropna(subset=["cod_curso"])
    out["_k_ies_curso"] = out["cod_ies"].astype(str) + "|" + out["curso"].map(_norm)
    dest = os.path.join(BASE, "cursos_emec.parquet")
    out.to_parquet(dest, index=False)
    print(f"[emec] cursos_emec.parquet: {len(out):,} cursos "
          f"({os.path.getsize(dest)/1e6:.1f} MB) | situacao: "
          f"{out['situacao_emec'].value_counts().to_dict()}")

    ies = df[["CODIGO_IES", "NOME_IES"]].dropna().drop_duplicates()
    ies["_n"] = ies["NOME_IES"].map(_norm)
    ies["cod"] = pd.to_numeric(ies["CODIGO_IES"], errors="coerce").astype("Int64")
    amb = ies.groupby("_n")["cod"].nunique()
    unicos = set(amb[amb == 1].index)
    tab = (ies[ies["_n"].isin(unicos)].drop_duplicates("_n")[["_n", "cod", "NOME_IES"]]
           .rename(columns={"_n": "nome_norm", "cod": "cod_ies", "NOME_IES": "nome_ies"}))
    dest2 = os.path.join(BASE, "ies_emec.parquet")
    tab.to_parquet(dest2, index=False)
    print(f"[emec] ies_emec.parquet: {len(tab):,} nomes inequivocos "
          f"({int((amb > 1).sum())} ambiguos descartados)")
    print("[emec] agora: git add cursos_emec.parquet ies_emec.parquet && git commit")


if __name__ == "__main__":
    caminho = sys.argv[1] if len(sys.argv) > 1 else CSV_PADRAO
    if not os.path.exists(caminho):
        sys.exit(f"CSV nao encontrado: {caminho}\nBaixe no navegador (ver docstring).")
    gerar(caminho)
