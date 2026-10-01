# -*- coding: utf-8 -*-
"""Le o historico de beneficiarios para o formato longo que o Blast usa.

Tres fontes, que viram tres secoes do e-mail:

    benefs_historico_ss.csv     medico     todos os segmentos somados por registro
                                corporate  SO o segmento "Coletivo Empresarial"
    odonto_historico_ss.csv     odonto
    benefs_historico_total.csv  Market     a linha agregada da ANS (nao e soma de
                                           operadoras: inclui quem nao esta no de-para)

Formato de saida (o mesmo que vai para o BigQuery `sala_situacao_blast`):

    registro | ano | mes | segmento | beneficiarios | secao

`Competencia` vem em dois formatos nos arquivos da ANS — `MAI/2015` nos de
operadora e `ago/24` no de mercado. Os dois sao tratados aqui.
"""
from __future__ import annotations

import os
import re

import pandas as pd

MES_NUM = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
           "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
CORPORATE = "coletivo empresarial"


def _competencia(txt: str) -> tuple[int, int] | None:
    """`MAI/2015` e `ago/24` -> (ano, mes). Ano de 2 digitos vira 20xx."""
    m = re.match(r"\s*([A-Za-zçÇ]{3})[/\-](\d{2,4})\s*$", str(txt))
    if not m:
        return None
    mes = MES_NUM.get(m.group(1)[:3].lower())
    if not mes:
        return None
    ano = int(m.group(2))
    return (2000 + ano if ano < 100 else ano), mes


def _ler(caminho: str) -> pd.DataFrame:
    return pd.read_csv(caminho, sep=";", encoding="utf-8-sig", dtype=str)


def carregar(pasta: str) -> pd.DataFrame:
    """Junta os tres arquivos no formato longo."""
    partes = []

    for arq, secao in (("benefs_historico_ss.csv", "medico"),
                       ("odonto_historico_ss.csv", "odonto")):
        caminho = os.path.join(pasta, arq)
        if not os.path.exists(caminho):
            continue
        d = _ler(caminho)
        comp = d["Competencia"].map(_competencia)
        d = d.assign(
            ano=[c[0] if c else None for c in comp],
            mes=[c[1] if c else None for c in comp],
            registro=d["Registro"].str.strip().str.zfill(6),
            segmento=d["Segmento"].str.strip(),
            beneficiarios=pd.to_numeric(d["Beneficiarios"], errors="coerce"),
            secao=secao,
        ).dropna(subset=["ano", "mes", "beneficiarios"])
        partes.append(d[["registro", "ano", "mes", "segmento",
                         "beneficiarios", "secao"]])

    caminho = os.path.join(pasta, "benefs_historico_total.csv")
    if os.path.exists(caminho):
        d = _ler(caminho)
        comp = d["Competencia"].map(_competencia)
        d = d.assign(
            ano=[c[0] if c else None for c in comp],
            mes=[c[1] if c else None for c in comp],
            registro="MERCADO",
            segmento=d["Especialidade"].str.strip(),
            beneficiarios=pd.to_numeric(d["Beneficiarios"], errors="coerce"),
            secao="mercado",
        ).dropna(subset=["ano", "mes", "beneficiarios"])
        partes.append(d[["registro", "ano", "mes", "segmento",
                         "beneficiarios", "secao"]])

    if not partes:
        raise SystemExit(f"nenhum CSV de historico encontrado em {pasta}")
    out = pd.concat(partes, ignore_index=True)
    out["ano"] = out["ano"].astype(int)
    out["mes"] = out["mes"].astype(int)
    return out


def para_secao(df: pd.DataFrame, secao: str) -> pd.DataFrame:
    """Recorta e agrega o longo para o que cada secao do e-mail precisa."""
    if secao == "corporate":
        d = df[(df["secao"] == "medico")
               & (df["segmento"].str.lower() == CORPORATE)]
    elif secao in ("medico", "odonto"):
        d = df[df["secao"] == secao]
    else:
        raise ValueError(secao)
    return (d.groupby(["registro", "ano", "mes"], as_index=False)["beneficiarios"]
             .sum())


def mercado(df: pd.DataFrame, secao: str) -> pd.DataFrame:
    """A linha agregada da ANS para a secao (Assistencia Medica / Odontologico)."""
    alvo = "exclusivamente odontol" if secao == "odonto" else "assist"
    d = df[(df["secao"] == "mercado")
           & (df["segmento"].str.lower().str.startswith(alvo))]
    return (d.groupby(["registro", "ano", "mes"], as_index=False)["beneficiarios"]
             .sum())
