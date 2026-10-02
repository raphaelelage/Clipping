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
# Nomes de exibicao das secoes na planilha (o codigo segue usando as chaves)
ROTULO_SECAO = {"medico": "Médico-hospitalar", "odonto": "Odontológico",
                "mercado": "Mercado Total"}


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
def no_github() -> bool:
    """True quando rodando no runner do GitHub — la o tempo e cota paga, entao
    so a fase 1 roda. No PC o tempo e de graca e vale varrer tudo."""
    return bool(os.environ.get("GITHUB_ACTIONS"))


def obter_historico(so_tabela: bool, sem_bq: bool, ano=None, mes=None,
                    todas: bool = False):
    """Devolve (df_longo, origem, novidade).

    `novidade` e None quando nao da para saber (--so-tabela, --sem-bq) e, quando
    da, diz se a coleta trouxe mes NOVO em relacao ao que ja estava no BigQuery.
    O dono quer o e-mail AVISANDO que nao houve dado novo — melhor do que silencio
    (que se confunde com falha) e melhor do que mandar a tabela do mes velho como
    se fosse noticia."""
    if so_tabela:
        if sem_bq:
            raise SystemExit("--so-tabela precisa do BigQuery (ou use --sem-bq "
                             "junto com uma coleta)")
        import blast_bq
        _p("[bq] lendo histórico…")
        return blast_bq.ler(ano, mes), "BigQuery", None

    antes = None
    if not sem_bq:
        import blast_bq
        try:
            antes = blast_bq.ultimo_mes()
        except Exception as exc:                                  # noqa: BLE001
            _p(f"[bq] não consegui ler o último mês ({str(exc)[:70]})")

    import blast_coleta
    _p("[coleta] Sala de Situação…")
    df = blast_coleta.coletar(log=_p, todas=todas)
    agora = mes_de_referencia(df)
    novidade = None if antes is None else (agora > antes)
    if antes is not None:
        _p(f"[bq] último mês gravado: {antes[0]}-{antes[1]:02d} · "
           f"coletado agora: {agora[0]}-{agora[1]:02d} · "
           f"{'DADO NOVO' if novidade else 'sem dado novo'}")
    if not sem_bq:
        import blast_bq
        blast_bq.gravar(df, log=_p)
    return df, "Sala de Situação", novidade


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


# --------------------------------------------------------------- e-mail
def destinatarios() -> list[str]:
    bruto = (os.environ.get("BLAST_TO") or os.environ.get("EMAIL_TO_OVERRIDE")
             or "raphael.elage.s@gmail.com")
    return [e.strip() for e in bruto.replace(";", ",").split(",") if e.strip()]


def enviar(html: str, anexo: str, ano: int, mes: int, para: list[str],
           sem_novidade: bool = False, fase: int = 1) -> bool:
    user = os.environ.get("EMAIL_REMETENTE", "").strip()
    pwd = os.environ.get("EMAIL_SENHA", "").replace(" ", "").strip()
    if not (user and pwd):
        _p("[email] EMAIL_REMETENTE/EMAIL_SENHA ausentes — não enviado")
        return False
    msg = EmailMessage()
    msg["Subject"] = (("[sem dado novo] " if sem_novidade else "")
                      + f"ANS Net Adds — {bp.rotulo_mes(ano, mes)}"
                      + ("" if fase == 1 else " (base completa)"))
    msg["From"] = user
    msg["To"] = ", ".join(para)
    msg.set_content("Tabelas de net adds da Sala de Situação da ANS. "
                    "Planilha completa em anexo.")
    msg.add_alternative(html, subtype="html")
    if anexo and os.path.exists(anexo):
        with open(anexo, "rb") as f:
            zipado = anexo.lower().endswith(".zip")
            msg.add_attachment(
                f.read(), maintype="application",
                subtype=("zip" if zipado else
                         "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                filename=os.path.basename(anexo))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.send_message(msg)
    _p(f"[ok] e-mail enviado para {', '.join(para)}")
    return True


# --------------------------------------------------------------- main
def _uma_fase(a, fase: int, alvo):
    """Roda uma fase inteira: dados -> tabelas -> planilha -> e-mail."""
    todas = (fase == 2)
    df, origem, novidade = obter_historico(a.so_tabela, a.sem_bq,
                                           *(alvo or (None, None)), todas=todas)
    if df is None or not len(df):
        raise SystemExit("histórico vazio — nada a montar")
    ano, mes = alvo or mes_de_referencia(df)
    _p(f"[blast] fase {fase} | {origem} | referência {bp.rotulo_mes(ano, mes)} | "
       f"{len(df):,} linhas")

    if fase == 2 and not a.so_tabela:
        import blast_coleta
        _p("[quebras] faixa etária e UF do mês corrente…")
        try:
            q = blast_coleta.coletar_quebras(
                sorted(blast_coleta.registros_do_config()), ano, mes, log=_p)
            if len(q):
                df = pd.concat([df, q], ignore_index=True)
        except Exception as exc:                                  # noqa: BLE001
            _p(f"[quebras] falharam: {str(exc)[:90]}")
        try:
            m = blast_coleta.coletar_mercado(log=_p)
            if len(m):
                df = pd.concat([df, m], ignore_index=True)
        except Exception as exc:                                  # noqa: BLE001
            _p(f"[mercado] falhou: {str(exc)[:90]}")

    avisos = []
    if novidade is False:
        avisos.append(f"A ANS ainda não publicou mês novo — o último disponível "
                      f"continua sendo {bp.rotulo_mes(ano, mes)}. As tabelas "
                      f"abaixo são as mesmas da rodada anterior.")
    tabelas, probs = montar_tudo(df, ano, mes)
    avisos += probs
    if not tabelas:
        raise SystemExit("nenhuma tabela montada: " + "; ".join(avisos))
    if fase == 2:
        avisos.append("Base completa: todas as operadoras do painel, mais as "
                      "quebras por faixa etária e UF do mês corrente. Faixa "
                      "etária e UF sao marginais — nao cruzam entre si nem com "
                      "o historico.")

    sufixo = "" if fase == 1 else " completo"
    destino = a.saida or os.path.join(
        AQUI, f"ANS_Net_Adds_Blast ({bp.rotulo_mes(ano, mes)}{sufixo}).xlsx")
    br.excel(destino, df, tabelas)
    _p(f"[ok] planilha: {destino}")
    anexo = destino
    if os.path.getsize(destino) > 20 * 1024 * 1024:
        anexo = br.zipar(destino)
        _p(f"[ok] zipada: {os.path.getsize(destino)/1e6:.1f} MB -> "
           f"{os.path.getsize(anexo)/1e6:.1f} MB (o Gmail corta em 25 MB)")

    html = br.email_html(tabelas, bp.rotulo_mes(ano, mes), avisos)
    io.open(os.path.splitext(destino)[0] + ".html", "w",
            encoding="utf-8").write(html)
    if not a.sem_email:
        enviar(html, anexo, ano, mes, destinatarios(),
               sem_novidade=(novidade is False), fase=fase)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--so-tabela", action="store_true",
                    help="usa o histórico do BQ, sem coletar")
    ap.add_argument("--mes", help="AAAA-MM (padrão: o último disponível)")
    ap.add_argument("--sem-email", action="store_true")
    ap.add_argument("--sem-bq", action="store_true")
    ap.add_argument("--saida", default=None)
    ap.add_argument("--fase", choices=["1", "2", "auto"], default="auto",
                    help="1 = só os grupos (rápido) · 2 = todas as operadoras + "
                         "quebras · auto = 1 e depois 2 no PC, só 1 no GitHub")
    a = ap.parse_args(argv)

    alvo = None
    if a.mes:
        y, m = a.mes.split("-")
        alvo = (int(y), int(m))

    if a.fase == "auto":
        fases = [1] if (no_github() or a.so_tabela) else [1, 2]
    else:
        fases = [int(a.fase)]
    _p(f"[blast] fases: {fases} "
       f"({'GitHub' if no_github() else 'PC'})")

    for f in fases:
        _uma_fase(a, f, alvo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
