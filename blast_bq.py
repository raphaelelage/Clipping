# -*- coding: utf-8 -*-
"""O historico do Blast no BigQuery — dataset `sala_situacao_blast`.

Por que o historico mora aqui e nao num CSV: a tabela do e-mail precisa de 13
meses por operadora para fechar QTD/YTD/YoY, e o botao "rodar so a tabela" do app
tem que montar o e-mail SEM tocar na Sala de Situacao. O BQ e o que torna as duas
coisas possiveis.

Uma tabela so, formato longo:

    registro STRING  ano INT  mes INT  segmento STRING  beneficiarios FLOAT
    secao STRING ('medico' | 'odonto' | 'mercado')
    dimensao STRING ('Contratação' | 'Faixa etária' | 'UF')
    coletado_em TIMESTAMP

A carga e por MES: apagar o mes e regravar. Assim re-rodar o mesmo mes nao
duplica, e um mes revisado pela ANS entra por cima do antigo.

`dimensao` existe para a fase 2 (base completa) caber aqui: faixa etaria e UF
sao MARGINAIS do mesmo total, entao quem le tem que filtrar uma dimensao so —
somar as tres conta a mesma vida tres vezes. Sem esta coluna o `gravar` jogava
as quebras no lixo em silencio, e o segundo e-mail nunca podia ser montado
longe da maquina que coletou (visto em 02/10/2026).
"""
from __future__ import annotations

import os

PROJETO = os.environ.get("BQ_PROJECT", "ans-net-adds-big-query")
DATASET = "sala_situacao_blast"
TABELA = "beneficiarios"
FQN = f"{PROJETO}.{DATASET}.{TABELA}"

ESQUEMA = [("registro", "STRING"), ("ano", "INTEGER"), ("mes", "INTEGER"),
           ("segmento", "STRING"), ("beneficiarios", "FLOAT"),
           ("secao", "STRING"), ("dimensao", "STRING"),
           ("coletado_em", "TIMESTAMP")]
# colunas acrescentadas depois da tabela existir — `create_table(exists_ok)` nao
# as adiciona, e um ALTER idempotente resolve sem migracao a mao
NOVAS = [("dimensao", "STRING")]


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
    for nome, tipo in NOVAS:
        c.query(f"ALTER TABLE `{FQN}` ADD COLUMN IF NOT EXISTS "
                f"{nome} {tipo}").result()
    return c


def gravar(df, c=None, log=print):
    """Grava o longo, substituindo cada (ano, mes) que vier no DataFrame."""
    import pandas as pd
    from google.cloud import bigquery
    c = garantir(c)
    d = df.copy()
    if "coletado_em" not in d.columns:
        d["coletado_em"] = pd.Timestamp.utcnow()
    if "dimensao" not in d.columns:
        # base antiga, so contratacao — marcar explicitamente e melhor do que
        # deixar nulo, que depois obriga todo leitor a tratar o NaN
        d["dimensao"] = "Contratação"
    meses = sorted({(int(a), int(m)) for a, m in zip(d["ano"], d["mes"])})
    if meses:
        # UM delete, nao um por mes: a carga traz 135 meses, e 135 queries em
        # sequencia levavam quase todo o tempo do run no GitHub (04/10/2026)
        chaves = ", ".join(str(a * 100 + m) for a, m in meses)
        c.query(f"DELETE FROM `{FQN}` "
                f"WHERE ano * 100 + mes IN ({chaves})").result()
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
    # `dimensao` nasceu depois da tabela. Quem le NAO cria coluna (o `ler` roda
    # no GitHub, onde a conta pode nao ter DDL), entao o SELECT se adapta ao que
    # a tabela tem — senao a primeira rodada `--so-tabela` apos o deploy
    # morreria com "Unrecognized name: dimensao".
    tem = {f.name for f in c.get_table(FQN).schema}
    dim = ("IFNULL(dimensao, 'Contratação')" if "dimensao" in tem
           else "'Contratação'")
    return c.query(f"SELECT registro, ano, mes, segmento, beneficiarios, secao, "
                   f"{dim} AS dimensao "
                   f"FROM `{FQN}` {onde}").to_dataframe()


def ultimo_mes(c=None):
    """(ano, mes) mais recente no BQ, ou None se a tabela estiver vazia."""
    c = c or cliente()
    r = list(c.query(f"SELECT MAX(ano*100+mes) v FROM `{FQN}`").result())
    v = r[0]["v"] if r else None
    return (v // 100, v % 100) if v else None
