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
import blast_textos as tx

AQUI = os.path.dirname(os.path.abspath(__file__))
COLUNAS_CFG = os.path.join(AQUI, "blast_colunas.json")
LIMITE_ANEXO = 24 * 1024 * 1024        # o Gmail corta em 25 MB
SECOES = [("medico", "Health Plans ('000)"),
          ("odonto", "Dental Plans ('000)"),
          ("corporate_medico", "Corporate Health Plans ('000)"),
          ("corporate_odonto", "Corporate Dental Plans ('000)")]
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
    return bp.das_chaves(escolha, ano, mes) or padrao


# --------------------------------------------------------------- dados
def no_github() -> bool:
    """True quando rodando no runner do GitHub — la o tempo e cota paga, entao
    so a fase 1 roda. No PC o tempo e de graca e vale varrer tudo."""
    return bool(os.environ.get("GITHUB_ACTIONS"))


def obter_historico(so_tabela: bool, sem_bq: bool, ano=None, mes=None,
                    todas: bool = False, quebras: bool = False):
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
    if quebras:
        # antes do gravar, de proposito: a carga do BQ apaga e regrava o mes
        # inteiro, entao as quebras tem que entrar na MESMA remessa — num
        # segundo `gravar` elas apagariam a contratacao que acabou de subir
        df = _com_quebras(df, *agora)
    novidade = None if antes is None else (agora > antes)
    if antes is not None:
        _p(f"[bq] último mês gravado: {antes[0]}-{antes[1]:02d} · "
           f"coletado agora: {agora[0]}-{agora[1]:02d} · "
           f"{'DADO NOVO' if novidade else 'sem dado novo'}")
    if not sem_bq:
        import blast_bq
        blast_bq.gravar(df, log=_p)
    return df, "Sala de Situação", novidade


def _com_quebras(df: pd.DataFrame, ano: int, mes: int) -> pd.DataFrame:
    """Junta faixa etaria, UF e o mercado por dimensao ao longo da contratacao.

    So do mes corrente — e o que a Sala de Situacao oferece. Falha de uma quebra
    nao derruba a rodada: a tabela do e-mail nao depende delas."""
    import blast_coleta
    _p("[quebras] faixa etária e UF do mês corrente…")
    partes = [df]
    try:
        q = blast_coleta.coletar_quebras(
            sorted(blast_coleta.registros_do_config()), ano, mes, log=_p)
        if len(q):
            partes.append(q)
    except Exception as exc:                                      # noqa: BLE001
        _p(f"[quebras] falharam: {str(exc)[:90]}")
    try:
        m = blast_coleta.coletar_mercado(log=_p)
        if len(m):
            partes.append(m)
    except Exception as exc:                                      # noqa: BLE001
        _p(f"[mercado] falhou: {str(exc)[:90]}")
    return pd.concat(partes, ignore_index=True) if len(partes) > 1 else df


def mes_de_referencia(df: pd.DataFrame) -> tuple[int, int]:
    ano = int(df["ano"].max())
    return ano, int(df[df["ano"] == ano]["mes"].max())


# --------------------------------------------------------------- montagem
def montar_tudo(df: pd.DataFrame, ano: int, mes: int):
    """(tabelas, avisos, series). `series` sao as mesmas instancias que as
    tabelas usaram — o texto do WhatsApp recorre a elas para periodo que nao
    virou coluna do mes, sem abrir um segundo caminho de calculo."""
    colunas = colunas_do_mes(ano, mes)
    grupos = bt.carregar_grupos()
    tabelas, avisos, series = [], [], {}
    for secao, titulo in SECOES:
        try:
            serie = bt.Serie(bh.para_secao(df, secao))
            merc = bh.mercado(df, secao)
            sm = bt.Serie(merc) if len(merc) else None
            tabelas.append((bt.montar(serie, secao, ano, mes, colunas,
                                      grupos=grupos, serie_mercado=sm), titulo))
            series[secao] = (serie, sm)
        except Exception as exc:                                  # noqa: BLE001
            avisos.append(f"{titulo}: {str(exc)[:120]}")
    return tabelas, avisos, series


def texto_do_mes(tabelas, series, ano: int, mes: int):
    """O rascunho de WhatsApp preenchido. (texto, avisos) — nunca levanta:
    e-mail sem texto ainda serve; e-mail que nao sai, nao."""
    try:
        modelo = tx.do_mes(mes)
        ctx = tx.Contexto(tabelas, series, ano, mes)
        texto, probs = tx.aplicar(modelo, ctx)
        if probs:
            probs = [f"Texto: {m}" for m in dict.fromkeys(probs)]
        return texto, probs
    except Exception as exc:                                      # noqa: BLE001
        return "", [f"Texto: não consegui montar ({str(exc)[:90]})"]


def previa_texto(modelo: str):
    """(texto, erro) — a aba Textos do app preenchendo as marcas de verdade.

    Le os ultimos meses do BigQuery e monta as tabelas, para o numero da previa
    ser o mesmo que iria no e-mail. Precisa de credencial do Google: no PC tem,
    no Streamlit Cloud nao — e por isso devolve `erro` em texto em vez de
    estourar."""
    try:
        import blast_bq
        alvo = blast_bq.ultimo_mes()
        if not alvo:
            return "", "o BigQuery está vazio — rode a coleta uma vez primeiro."
        ano, mes = alvo
        df = blast_bq.ler(ano, mes, meses=18)
        tabelas, _avisos, series = montar_tudo(df, ano, mes)
        if not tabelas:
            return "", "não consegui montar as tabelas com o que está no BQ."
        texto, probs = tx.aplicar(modelo, tx.Contexto(tabelas, series, ano, mes))
        if probs:
            texto += "\n\n[avisos] " + " · ".join(dict.fromkeys(probs))
        return texto, None
    except Exception as exc:                                      # noqa: BLE001
        return "", (f"não consegui ler o BigQuery daqui ({str(exc)[:140]}). "
                    f"Rode a prévia no seu PC, ou veja o texto pronto no e-mail.")


# --------------------------------------------------------------- e-mail
def destinatarios() -> list[str]:
    bruto = (os.environ.get("BLAST_TO") or os.environ.get("EMAIL_TO_OVERRIDE")
             or "raphael.elage.s@gmail.com")
    return [e.strip() for e in bruto.replace(";", ",").split(",") if e.strip()]


def enviar(html: str, anexo: str, ano: int, mes: int, para: list[str],
           sem_novidade: bool = False, fase: int = 1, texto: str = "") -> bool:
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
    # a parte de texto puro e o rascunho do WhatsApp: no celular da para
    # selecionar e copiar direto do e-mail, sem abrir o app
    msg.set_content(texto or "Tabelas de net adds da Sala de Situação da ANS. "
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
    df, origem, novidade = obter_historico(
        a.so_tabela, a.sem_bq, *(alvo or (None, None)), todas=todas,
        quebras=(fase == 2 and not a.so_tabela))
    if df is None or not len(df):
        raise SystemExit("histórico vazio — nada a montar")
    ano, mes = alvo or mes_de_referencia(df)
    _p(f"[blast] fase {fase} | {origem} | referência {bp.rotulo_mes(ano, mes)} | "
       f"{len(df):,} linhas")

    avisos = []
    if novidade is False:
        avisos.append(f"A ANS ainda não publicou mês novo — o último disponível "
                      f"continua sendo {bp.rotulo_mes(ano, mes)}. As tabelas "
                      f"abaixo são as mesmas da rodada anterior.")
    tabelas, probs, series = montar_tudo(df, ano, mes)
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
    if os.path.getsize(anexo) > LIMITE_ANEXO:
        # o Gmail recusa a mensagem inteira, nao so o anexo — melhor chegar sem
        # a planilha, com as tabelas no corpo, do que nao chegar
        avisos.append(f"A planilha ficou com "
                      f"{os.path.getsize(anexo)/1e6:.0f} MB e não cabe no "
                      f"e-mail; ela está no artefato da execução do GitHub "
                      f"(aba Debug do app) e salva no PC.")
        _p(f"[email] anexo de {os.path.getsize(anexo)/1e6:.1f} MB acima do "
           f"limite — enviando sem anexo")
        anexo = None

    texto, probs_tx = texto_do_mes(tabelas, series, ano, mes)
    avisos += probs_tx
    if texto:
        txt = os.path.splitext(destino)[0] + ".txt"
        io.open(txt, "w", encoding="utf-8").write(texto)
        _p(f"[ok] rascunho ({tx.POSICOES[tx.escolher(mes)]}): {txt}")

    html = br.email_html(tabelas, bp.rotulo_mes(ano, mes), avisos, texto=texto)
    # o arquivo precisa do charset; o e-mail nao, porque o MIME ja declara
    # utf-8. Sem isso o .html do artefato abre com "SituaÃ§Ã£o" (04/10/2026).
    io.open(os.path.splitext(destino)[0] + ".html", "w",
            encoding="utf-8").write('<!doctype html><meta charset="utf-8">'
                                    + html)
    if not a.sem_email:
        enviar(html, anexo, ano, mes, destinatarios(),
               sem_novidade=(novidade is False), fase=fase, texto=texto)
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
