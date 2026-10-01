# -*- coding: utf-8 -*-
"""Blast de Net Adds da ANS — do painel ao e-mail.

    python blast_run.py                      coleta a Sala, grava no BQ e manda
    python blast_run.py --so-tabela          NAO coleta: usa o que ja esta no BQ
    python blast_run.py --mes 2026-07        monta para um mes especifico
    python blast_run.py --sem-email          so gera o arquivo, nao envia
    python blast_run.py --sem-bq             nao toca no BigQuery (teste local)

O `--so-tabela` existe para refazer o e-mail depois de mexer nos grupos ou nas
colunas, sem bater de novo na ANS — e por isso que o historico mora no BigQuery
(`sala_situacao_blast`) e nao num CSV ao lado.

Variaveis de ambiente: EMAIL_REMETENTE, EMAIL_SENHA (app password do Gmail),
BLAST_TO (destinatarios, virgula) ou EMAIL_TO_OVERRIDE.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import smtplib
import ssl
import sys
from datetime import date
from email.message import EmailMessage

import pandas as pd

import blast_historico as bh
import blast_periodos as bp
import blast_render as br
import blast_tabela as bt

AQUI = os.path.dirname(os.path.abspath(__file__))
COLUNAS_CFG = os.path.join(AQUI, "blast_colunas.json")
SECOES = [("medico", "Health plans ('000)"),
          ("odonto", "Dental plans ('000)"),
          ("corporate", "Corporate ('000)")]


def _p(msg):
    print(msg, flush=True)


# --------------------------------------------------------------- colunas
def colunas_do_mes(ano: int, mes: int) -> list[dict]:
    """As 4 colunas: o override do app, se houver, senao a regra do calendario."""
    padrao = bp.padrao(ano, mes)
    if not os.path.exists(COLUNAS_CFG):
        return padrao
    try:
        cfg = json.load(io.open(COLUNAS_CFG, encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        return padrao
    escolha = (cfg.get("meses") or {}).get(f"{mes:02d}")
    if not escolha:
        return padrao
    opcoes = {o["rotulo"]: o for o in bp.opcoes(ano, mes)}
    out = [opcoes.get(r) for r in escolha]
    return [c for c in out if c] or padrao


# --------------------------------------------------------------- dados
def obter_historico(so_tabela: bool, sem_bq: bool, ano=None, mes=None):
    """Devolve (df_longo, origem). Coleta ou le do BQ, conforme o modo."""
    if so_tabela:
        if sem_bq:
            raise SystemExit("--so-tabela precisa do BigQuery (ou use --sem-bq "
                             "junto com uma coleta)")
        import blast_bq
        _p("[bq] lendo histórico…")
        return blast_bq.ler(ano, mes), "BigQuery"

    import blast_coleta
    _p("[coleta] Sala de Situação…")
    df = blast_coleta.coletar(log=_p)
    if not sem_bq:
        import blast_bq
        blast_bq.gravar(df, log=_p)
    return df, "Sala de Situação"


def mes_de_referencia(df: pd.DataFrame) -> tuple[int, int]:
    ano = int(df["ano"].max())
    return ano, int(df[df["ano"] == ano]["mes"].max())


# --------------------------------------------------------------- montagem
def montar_tudo(df: pd.DataFrame, ano: int, mes: int):
    colunas = colunas_do_mes(ano, mes)
    grupos = bt.carregar_grupos()
    tabelas, avisos = [], []
    for secao, titulo in SECOES:
        try:
            serie = bt.Serie(bh.para_secao(df, secao))
            merc = bh.mercado(df, secao)
            sm = bt.Serie(merc) if len(merc) else None
            tabelas.append((bt.montar(serie, secao, ano, mes, colunas,
                                      grupos=grupos, serie_mercado=sm), titulo))
        except Exception as exc:                                  # noqa: BLE001
            avisos.append(f"{titulo}: {str(exc)[:120]}")
    return tabelas, avisos


def abas_historico(df: pd.DataFrame) -> dict:
    """As 3 bases que hoje saem em arquivos separados, agora como 3 abas."""
    return {
        "Med_Benefs_Historico": df[df["secao"] == "medico"],
        "Odonto_Benefs_Historico": df[df["secao"] == "odonto"],
        "Market_Historico": df[df["secao"] == "mercado"],
    }


# --------------------------------------------------------------- e-mail
def destinatarios() -> list[str]:
    bruto = (os.environ.get("BLAST_TO") or os.environ.get("EMAIL_TO_OVERRIDE")
             or "raphael.elage.s@gmail.com")
    return [e.strip() for e in bruto.replace(";", ",").split(",") if e.strip()]


def enviar(html: str, anexo: str, ano: int, mes: int, para: list[str]) -> bool:
    user = os.environ.get("EMAIL_REMETENTE", "").strip()
    pwd = os.environ.get("EMAIL_SENHA", "").replace(" ", "").strip()
    if not (user and pwd):
        _p("[email] EMAIL_REMETENTE/EMAIL_SENHA ausentes — não enviado")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"ANS Net Adds — {bp.rotulo_mes(ano, mes)}"
    msg["From"] = user
    msg["To"] = ", ".join(para)
    msg.set_content("Tabelas de net adds da Sala de Situação da ANS. "
                    "Planilha completa em anexo.")
    msg.add_alternative(html, subtype="html")
    if anexo and os.path.exists(anexo):
        with open(anexo, "rb") as f:
            msg.add_attachment(
                f.read(), maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=os.path.basename(anexo))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.send_message(msg)
    _p(f"[ok] e-mail enviado para {', '.join(para)}")
    return True


# --------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-tabela", action="store_true",
                    help="usa o histórico do BQ, sem coletar")
    ap.add_argument("--mes", help="AAAA-MM (padrão: o último disponível)")
    ap.add_argument("--sem-email", action="store_true")
    ap.add_argument("--sem-bq", action="store_true")
    ap.add_argument("--saida", default=None)
    a = ap.parse_args(argv)

    alvo = None
    if a.mes:
        y, m = a.mes.split("-")
        alvo = (int(y), int(m))

    df, origem = obter_historico(a.so_tabela, a.sem_bq,
                                 *(alvo or (None, None)))
    if df is None or not len(df):
        raise SystemExit("histórico vazio — nada a montar")
    ano, mes = alvo or mes_de_referencia(df)
    _p(f"[blast] {origem} | referência {bp.rotulo_mes(ano, mes)} | "
       f"{len(df):,} linhas")

    tabelas, avisos = montar_tudo(df, ano, mes)
    if not tabelas:
        raise SystemExit("nenhuma tabela montada: " + "; ".join(avisos))
    _p(f"[blast] {len(tabelas)} tabela(s) | colunas "
       f"{tabelas[0][0]['colunas']}")
    for av in avisos:
        _p(f"[aviso] {av}")

    destino = a.saida or os.path.join(
        AQUI, f"ANS_Net_Adds_Blast ({bp.rotulo_mes(ano, mes)}).xlsx")
    br.excel(destino, abas_historico(df), tabelas)
    _p(f"[ok] planilha: {destino}")

    html = br.email_html(tabelas, bp.rotulo_mes(ano, mes), avisos)
    io.open(os.path.splitext(destino)[0] + ".html", "w",
            encoding="utf-8").write(html)

    if not a.sem_email:
        enviar(html, destino, ano, mes, destinatarios())
    return 0


if __name__ == "__main__":
    sys.exit(main())
