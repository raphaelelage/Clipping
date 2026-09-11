"""Regenera cursos_inep.parquet a partir do zip de microdados do Censo da Educacao
Superior (INEP) — 100% deterministico, SEM IA (dono, 11/09/2026).

PASSO MANUAL (1x/ano, quando sai o novo Censo): baixar o zip de microdados em
https://www.inep.gov.br/ (Censo da Educacao Superior > Microdados) e rodar:

    python atualizar_inep.py <caminho_do_zip>

Receita (validada em 11/09/2026 contra o parquet original: vagas e ingressantes
100% identicos nos 46.150 cursos):
  - 1 linha por CO_CURSO. Presencial usa a dimensao TOTAL (TP_DIMENSAO=1); curso EAD
    nao tem dim=1 e SOMA as linhas de polo (vagas/ingressantes);
  - municipio/uf SO quando ha UM valor (EAD multi-polo fica vazio). ATENCAO: o
    parquet da 1a geracao gravava o PRIMEIRO polo do arquivo como municipio do curso
    EAD — polo arbitrario e informacao enganosa; esta versao corrige (vazio > errado);
  - nome da IES vem do CSV de IES do proprio zip (join por CO_IES).
Depois: git add cursos_inep.parquet && git commit.
"""
import os
import sys
import zipfile

import pandas as pd


def gerar(zip_path):
    z = zipfile.ZipFile(zip_path)
    nomes = [n for n in z.namelist() if n.upper().endswith(".CSV")]
    cur = next(n for n in nomes if "CURSOS" in n.upper())
    ies_csv = next(n for n in nomes if "IES" in n.upper() and "CURSOS" not in n.upper())
    print(f"[inep] cursos: {cur.split('/')[-1]} | ies: {ies_csv.split('/')[-1]}")

    use = ["CO_CURSO", "CO_IES", "NO_CURSO", "SG_UF", "NO_MUNICIPIO", "QT_VG_TOTAL",
           "QT_ING", "TP_MODALIDADE_ENSINO", "TP_GRAU_ACADEMICO", "TP_DIMENSAO"]
    df = pd.read_csv(z.open(cur), sep=";", encoding="latin-1", usecols=use,
                     low_memory=False)
    print(f"[inep] {len(df):,} linhas, {df['CO_CURSO'].nunique():,} cursos")

    tem1 = df.loc[df["TP_DIMENSAO"] == 1, "CO_CURSO"].unique()
    base = pd.concat([df[df["TP_DIMENSAO"] == 1],
                      df[~df["CO_CURSO"].isin(tem1)]])

    def _unico(x):
        return x.iloc[0] if x.nunique(dropna=True) == 1 else ""
    agg = base.groupby("CO_CURSO").agg(
        cod_ies=("CO_IES", "first"), curso=("NO_CURSO", "first"),
        uf=("SG_UF", _unico), municipio=("NO_MUNICIPIO", _unico),
        vagas=("QT_VG_TOTAL", "sum"), ingressantes=("QT_ING", "sum"),
        modalidade=("TP_MODALIDADE_ENSINO", "first"),
        grau=("TP_GRAU_ACADEMICO", "first")).reset_index() \
        .rename(columns={"CO_CURSO": "cod_curso"})

    di = pd.read_csv(z.open(ies_csv), sep=";", encoding="latin-1",
                     usecols=["CO_IES", "NO_IES"], low_memory=False) \
        .drop_duplicates("CO_IES")
    agg = agg.merge(di.rename(columns={"CO_IES": "cod_ies", "NO_IES": "ies"}),
                    on="cod_ies", how="left")
    agg["ies"] = agg["ies"].fillna("")
    agg["cod_curso"] = agg["cod_curso"].astype(str)
    agg["cod_ies"] = agg["cod_ies"].astype(str)
    agg = agg[["cod_curso", "cod_ies", "ies", "curso", "uf", "municipio",
               "vagas", "ingressantes", "modalidade", "grau"]]

    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "cursos_inep.parquet")
    agg.to_parquet(dest, index=False)
    ead_vazio = int((agg["municipio"] == "").sum())
    print(f"[ok] cursos_inep.parquet: {len(agg):,} cursos "
          f"({os.path.getsize(dest)/1e6:.1f} MB) | municipio vazio (EAD multi-polo, "
          f"correto): {ead_vazio:,}")
    print("[inep] agora: git add cursos_inep.parquet && git commit")


if __name__ == "__main__":
    if len(sys.argv) < 2 or not os.path.exists(sys.argv[1]):
        sys.exit("uso: python atualizar_inep.py <microdados_censo_XXXX.zip>")
    gerar(sys.argv[1])
