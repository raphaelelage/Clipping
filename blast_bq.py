# -*- coding: utf-8 -*-
"""O historico do Blast no BigQuery — dataset `sala_situacao_blast`.

Por que o historico mora aqui e nao num CSV: a tabela do e-mail precisa de 13
meses por operadora para fechar QTD/YTD/YoY, e o botao "rodar so a tabela" do app
tem que montar o e-mail SEM tocar na Sala de Situacao. O BQ e o que torna as duas
coisas possiveis.

Uma tabela so, formato longo:

    registro STRING  ano INT  mes INT  segmento STRING  beneficiarios FLOAT
    secao STRING ('medico' | 'odonto' | 'mercado')  coletado_em TIMESTAMP

A carga e por MES: apagar o mes e regravar. Assim re-rodar o mesmo mes nao
duplica, e um mes revisado pela ANS entra por cima do antigo.
"""
from __future__ import annotations

import os

PROJETO = os.environ.get("BQ_PROJECT", "ans-net-adds-big-query")
DATASET = "sala_situacao_blast"
TABELA = "beneficiarios"
FQN = f"{PROJETO}.{DATASET}.{TABELA}"

ESQUEMA = [("registro", "STRING"), ("ano", "INTEGER"), ("mes", "INTEGER"),
           ("segmento", "STRING"), ("beneficiarios", "FLOAT"),
           ("secao", "STRING"), ("coletado_em", "TIMESTAMP")]


def cliente():
    from google.cloud import bigquery
    return bigquery.Client(project=PROJETO)


def garantir(c=None):
    """Cria dataset e tabela se faltarem. Idempotente."""
    from google.cloud import bigquery
    c = c or cliente()
    ds = bigquery.Dataset(f"{PROJETO}.{DATASET}")
    ds.location = os.environ.get("BQ_LOCATION", "US")
    c.create_dataset(ds, exists_ok=True)
    tb = bigquery.Table(FQN, schema=[bigquery.SchemaField(n, t) for n, t in ESQUEMA])
    tb.time_partitioning = bigquery.TimePartitioning(field=None)   # tabela pequena
    c.create_table(tb, exists_ok=True)
    return c


def gravar(df, c=None, log=print):
    """Grava o longo, substituindo cada (ano, mes) que vier no DataFrame."""
    import pandas as pd
    from google.cloud import bigquery
    c = garantir(c)
    d = df.copy()
    if "coletado_em" not in d.columns:
        d["coletado_em"] = pd.Timestamp.utcnow()
    meses = sorted({(int(a), int(m)) for a, m in zip(d["ano"], d["mes"])})
    for a, m in meses:
        c.query(f"DELETE FROM `{FQN}` WHERE ano={a} AND mes={m}").result()
    cfg = bigquery.LoadJobConfig(
        schema=[bigquery.SchemaField(n, t) for n, t in ESQUEMA],
        write_disposition="WRITE_APPEND")
    c.load_table_from_dataframe(d[[n for n, _ in ESQUEMA]], FQN,
                                job_config=cfg).result()
    log(f"[bq] {len(d):,} linhas em {len(meses)} mes(es) -> {FQN}")
    return len(d)


def ler(ate_ano: int | None = None, ate_mes: int | None = None, meses: int = 18,
        c=None):
    """O longo de volta, limitado aos ultimos `meses` ate (ate_ano, ate_mes)."""
    c = c or cliente()
    onde = ""
    if ate_ano and ate_mes:
        ini_a, ini_m = ate_ano, ate_mes - meses
        while ini_m <= 0:
            ini_m += 12
            ini_a -= 1
        onde = (f"WHERE (ano*100+mes) BETWEEN {ini_a*100+ini_m} "
                f"AND {ate_ano*100+ate_mes}")
    return c.query(f"SELECT registro, ano, mes, segmento, beneficiarios, secao "
                   f"FROM `{FQN}` {onde}").to_dataframe()


def ultimo_mes(c=None):
    """(ano, mes) mais recente no BQ, ou None se a tabela estiver vazia."""
    c = c or cliente()
    r = list(c.query(f"SELECT MAX(ano*100+mes) v FROM `{FQN}`").result())
    v = r[0]["v"] if r else None
    return (v // 100, v % 100) if v else None
