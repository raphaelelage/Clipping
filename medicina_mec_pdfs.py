"""Converte as duas planilhas OFICIAIS da SERES sobre cursos de Medicina em DataFrame:

  1. processos em tramitacao (administrativos + judicializados, item ii da MC ADC 81)
  2. processos sobrestados (item iii da MC ADC 81 — parados SEM decisao publicada)

Sao FOTOGRAFIAS (a versao baixada diz "Situação em 04 de junho de 2024"), nao historico.
Papel delas no levantamento:
  - CONTRAPROVA: processo deferido dessas listas deve aparecer como portaria no DOU;
    processo sobrestado NAO deve ter portaria de autorizacao publicada.
  - ENRIQUECIMENTO: trazem codigos (mantenedora, IES, curso), n. SEI, n. do processo
    judicial e regiao de saude — nada disso sai no texto do DOU.

Fonte (o caminho mudou em 2025/2026; o antigo areas-de-atuacao/... hoje da 404):
https://www.gov.br/mec/pt-br/assuntos/es/cursos-de-medicina/regulacao-e-supervisao/documentos
"""
import io
import re
import sys

import pandas as pd
import pdfplumber

COLS_TRAM = ["data_protocolo", "natureza", "tipo_processo", "regime_juridico", "ref_emec",
             "ref_sei", "ref_judicial", "cod_mantenedora", "mantenedora", "cod_ies", "ies",
             "municipio", "uf", "regiao_saude"]
COLS_SOBR = ["tipo_processo", "ref_emec", "ref_sei", "ref_judicial", "cod_mantenedora",
             "mantenedora", "cod_ies", "ies", "cod_curso", "curso", "municipio", "uf"]


def _limpa(c):
    return re.sub(r"\s+", " ", str(c or "")).strip()


def _tabelas(pdf_path, n_cols):
    """Concatena as tabelas de todas as paginas, pulando titulo e cabecalho repetido."""
    linhas = []
    with pdfplumber.open(pdf_path) as pdf:
        for pg in pdf.pages:
            for tb in pg.extract_tables() or []:
                for row in tb:
                    if row is None or len(row) != n_cols:
                        continue
                    vals = [_limpa(v) for v in row]
                    # pula titulo (celulas None), cabecalho e linhas vazias
                    if sum(1 for v in vals if v) < 3:
                        continue
                    if vals[0].lower().startswith(("tipo de processo", "data do")):
                        continue
                    if "sobrestados" in vals[0].lower() or "tramita" in vals[0].lower():
                        continue
                    linhas.append(vals)
    return linhas


def carregar(dir_pdfs="."):
    tram = pd.DataFrame(
        _tabelas(f"{dir_pdfs}/planilha-processos-de-cursos-de-medicina-em-tramitacao.pdf",
                 len(COLS_TRAM)), columns=COLS_TRAM)
    tram["situacao_mec"] = "em tramitacao"
    sobr = pd.DataFrame(
        _tabelas(f"{dir_pdfs}/planilha-processos-sobrestados-mc-adc-81.pdf",
                 len(COLS_SOBR)), columns=COLS_SOBR)
    sobr["situacao_mec"] = "sobrestado (MC ADC 81)"
    df = pd.concat([tram, sobr], ignore_index=True)
    df["fonte"] = "SERES/MEC — planilhas oficiais (situacao 04/06/2024)"
    return df


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    df = carregar(d)
    print(df["situacao_mec"].value_counts().to_string())
    df.to_parquet(f"{d}/medicina_mec_oficial.parquet")
    print(f"[ok] {len(df)} processos -> medicina_mec_oficial.parquet")
