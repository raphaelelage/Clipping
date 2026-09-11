"""VARREDURA COMPLETA do DOU por periodo — o pipeline inteiro, do zero, sem IA.

Pedido do dono (11/09/2026): "um codigo completo que pegaria tudo isso que voce fez
caso eu precisasse fazer do zero e de forma independente de IA; que principalmente NAO
deixasse passar absolutamente NENHUMA decisao nova no periodo; e pegasse o maximo de
informacao possivel para preencher a planilha".

O que uma varredura de [inicio, fim] faz, nesta ordem:
  1. COLETA dia a dia: dias uteis = secao do1 + edicao extra; fins de semana/feriados =
     so a extra (a secao regular nao circula, mas extra pode sair em qualquer dia).
     Dia que falhar (in.gov.br fora do ar) e RETENTADO no fim; se ainda falhar, entra
     NOMINALMENTE no relatorio e no e-mail com prefixo [INCOMPLETO] — nunca passa em
     silencio. A garantia de cobertura e o coracao deste modulo.
  2. FILTRA atos do MEC relevantes (dou_extrair.relevante) e EXTRAI tudo: texto
     integral, tabelas explodidas em 1 linha por curso, prosa do Art. 1 (curso, vagas,
     IES, mantenedora, municipio), classificacao pelo VERBO do dispositivo (com Art. 2
     quando o Art. 1 e procedimental) — o classificador v5 completo.
  3. FUNDE no Excel com a chave completa (link+processo+curso+ies+municipio+vagas):
     linha ja existente nao duplica nem perde enriquecimento manual/INEP.
  4. REGISTRA a cobertura na aba Notas ("varredura_cobertura": periodo, dias ok/vazios/
     falhos, atos novos) — a planilha carrega a prova do que ja foi verificado.
  5. REGENERA Funil + graficos (funil.py puxa INEP, e-MEC, IBGE, cautelares, nota de
     fontes e celulas amarelas — nada e inventado).
  6. REPORTA por e-mail (opcional): resumo do periodo, novidades (Medicina primeiro)
     e a lista exata de qualquer dia nao coberto.

Uso:
  python dou_varredura.py --inicio 2026-01-01 [--fim 2026-09-11]
         [--arquivo Regulacao_Cursos.xlsx] [--email a@b.com,c@d.com]
  (fim omitido = hoje. Roda no PC ou no Actions — mesmo codigo.)

Tempo medido (11/09/2026): ~2,6s por dia util + ~4 min de Funil/graficos no final.
1 mes ~ 5 min | 1 ano ~ 15 min | 2018-hoje ~ 105 min.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd

import avisos
import dou_historico as dh
import dou_extrair as dx
import dou_alerta
from corrigir_base_dou import _nkey

ASSUNTO_COBERTURA = "varredura_cobertura"
ASSUNTO_STATE = "radar_ultima_checagem"


# ------------------------------------------------------------------ coleta com garantia
def _dias_do_periodo(inicio, fim):
    """[(dia, secoes_obrigatorias)]: dia util exige do1+extra; fds/feriado so a extra."""
    plano, d = [], inicio
    while d <= fim:
        plano.append((d, ("do1", "do1_extra") if d.weekday() < 5 else ("do1_extra",)))
        d += timedelta(days=1)
    return plano


def _coletar(inicio, fim, log):
    """Todos os atos do MEC do periodo + manifesto de cobertura por dia.
    Devolve (atos_mec, manifesto) — manifesto[dia] = 'ok' | 'vazio' | 'FALHA'."""
    plano = _dias_do_periodo(inicio, fim)
    atos, manifesto, refazer = [], {}, []
    for i, (d, secoes) in enumerate(plano):
        achou, falhou = 0, False
        for sec in secoes:
            arr = dh.atos_do_dia(d, sec)
            if arr is None:
                falhou = True
                continue
            for a in arr:
                if str(a.get("hierarchyStr", "")).startswith("Ministério da Educação"):
                    a["_dia"], a["_secao"] = d.isoformat(), sec
                    atos.append(a)
                    achou += 1
        manifesto[d] = "FALHA" if falhou else ("ok" if achou else "vazio")
        if falhou:
            refazer.append((d, secoes))
        if (i + 1) % 50 == 0:
            log(f"[varredura] {i + 1}/{len(plano)} dias ({d}) | "
                f"{len(atos)} atos MEC | falhas ate aqui: {len(refazer)}")
    # SEGUNDA CHANCE para os dias que falharam — a garantia de cobertura exige
    for d, secoes in refazer:
        falhou, achou = False, 0
        for sec in secoes:
            arr = dh.atos_do_dia(d, sec)
            if arr is None:
                falhou = True
                continue
            for a in arr:
                if str(a.get("hierarchyStr", "")).startswith("Ministério da Educação"):
                    a["_dia"], a["_secao"] = d.isoformat(), sec
                    atos.append(a)
                    achou += 1
        manifesto[d] = "FALHA" if falhou else ("ok" if achou else "vazio")
    return atos, manifesto


# ------------------------------------------------------------------ fusao com o Excel
def _chave6(link, proc, curso, ies, municipio, vagas):
    return "|".join((_nkey(link), _nkey(proc), _nkey(curso), _nkey(ies),
                     _nkey(municipio), _nkey(vagas)))


def _fundir(novas, caminho, manifesto, inicio, fim, log):
    """Acrescenta as linhas ineditas ao Excel e registra a cobertura na aba Notas.
    Nunca sobrescreve linha existente (enriquecimento manual/INEP fica intacto)."""
    xl = pd.ExcelFile(caminho)
    atos = xl.parse("Atos")

    def _col(df, nome):
        # df.get(nome, "") devolve STRING quando a coluna falta e o zip itera os
        # caracteres dela (ja causou duplicata em massa uma vez) — Series sempre
        return df[nome] if nome in df.columns else pd.Series([""] * len(df),
                                                             index=df.index)
    tem = set(_chave6(l, p, c, i, m, v) for l, p, c, i, m, v in zip(
        atos["link"], _col(atos, "processo"), _col(atos, "curso"),
        _col(atos, "ies"), _col(atos, "municipio"), _col(atos, "numero_vagas")))
    ineditas = novas[[_chave6(l, p, c, i, m, v) not in tem for l, p, c, i, m, v in zip(
        novas["link"], novas["processo"], novas["curso"], novas["ies"],
        novas["municipio"], novas["numero_vagas"])]] if len(novas) else novas
    log(f"[varredura] {len(novas)} linha(s) extraidas -> {len(ineditas)} inedita(s) "
        f"(o resto ja estava na base)")

    for c in ineditas.columns:
        if c not in atos.columns:
            atos[c] = ""
    ineditas = ineditas.reindex(columns=atos.columns, fill_value="")
    todas = pd.concat([atos, ineditas], ignore_index=True) if len(ineditas) else atos

    cu = todas["curso"].astype(str).str.upper()
    med = todas[cu.str.contains(r"\bMEDICINA\b", regex=True, na=False)
                & ~cu.str.contains("VETERIN", na=False)]

    outras = {n: xl.parse(n) for n in xl.sheet_names
              if n not in ("Atos", "Medicina", "Funil", "Graficos", "Graf_Dados")}
    notas = outras.get("Notas")
    n_falha = sum(1 for v in manifesto.values() if v == "FALHA")
    falhos = sorted(d.isoformat() for d, v in manifesto.items() if v == "FALHA")
    linha_cob = {"Assunto": ASSUNTO_COBERTURA,
                 "Descricao": (f"{inicio.isoformat()}..{fim.isoformat()} varrido em "
                               f"{date.today().isoformat()}: {len(manifesto)} dias, "
                               f"{sum(1 for v in manifesto.values() if v == 'ok')} com ato, "
                               f"{sum(1 for v in manifesto.values() if v == 'vazio')} vazios, "
                               f"{n_falha} FALHA{' (' + ', '.join(falhos) + ')' if falhos else ''}; "
                               f"{len(ineditas)} linha(s) nova(s)")}
    if notas is None or "Assunto" not in getattr(notas, "columns", []):
        notas = pd.DataFrame([linha_cob])
    else:
        notas = pd.concat([notas, pd.DataFrame([linha_cob])], ignore_index=True)
        # varredura completa ate hoje SEM falha: adianta o estado do radar diario
        if n_falha == 0 and fim >= date.today() - timedelta(days=1):
            sel = notas["Assunto"].astype(str).str.strip() == ASSUNTO_STATE
            notas = notas[~sel]
            notas = pd.concat([notas, pd.DataFrame([{
                "Assunto": ASSUNTO_STATE,
                "Descricao": (f"{fim.isoformat()} — ultima varredura concluida do DOU "
                              f"(atualizado pela varredura por periodo; NAO apagar)")}])],
                ignore_index=True)
    outras["Notas"] = notas

    with pd.ExcelWriter(caminho, engine="openpyxl",
                        date_format="DD/MM/YYYY", datetime_format="DD/MM/YYYY") as xw:
        todas.to_excel(xw, sheet_name="Atos", index=False)
        med.to_excel(xw, sheet_name="Medicina", index=False)
        for n, df in outras.items():
            df.to_excel(xw, sheet_name=n, index=False)
        for aba in ["Atos", "Medicina"] + list(outras):
            w = xw.book[aba]
            w.freeze_panes = "A2"
            w.auto_filter.ref = w.dimensions
    return len(ineditas), med


# ------------------------------------------------------------------ orquestrador
def varrer(inicio, fim=None, caminho="Regulacao_Cursos.xlsx", log=print):
    """Roda a varredura completa e devolve o manifesto-resumo (dict)."""
    fim = fim or date.today()
    if inicio > fim:
        raise ValueError(f"inicio {inicio} depois do fim {fim}")
    t0 = time.time()
    log(f"[varredura] periodo {inicio} .. {fim} | arquivo: {caminho}")

    atos_mec, manifesto = _coletar(inicio, fim, log)
    relevantes = [a for a in atos_mec if dx.relevante(a)]
    log(f"[varredura] {len(atos_mec)} atos do MEC | {len(relevantes)} relevantes "
        f"(regulacao de curso/IES)")

    if relevantes:
        linhas = dx.extrair(relevantes, workers=6, log=log)
        df = pd.DataFrame(linhas)
        for c in ("tipo_ato", "curso", "ies", "municipio", "uf", "vagas_num", "cod_ies",
                  "processo_emec", "ato", "link", "data_publicacao", "resumo_texto",
                  "mantenedora", "texto_inicio", "ref_judicial", "retificacao",
                  "fonte_detalhe", "orgao"):
            if c not in df.columns:
                df[c] = None
        novas = dou_alerta.para_formato_excel(df)
    else:
        novas = pd.DataFrame(columns=["link", "processo", "curso", "ies", "municipio",
                                      "numero_vagas"])

    n_novas, med = _fundir(novas, caminho, manifesto, inicio, fim, log)

    # Funil + graficos SEMPRE que a planilha foi regravada (a regravacao via pandas
    # apaga a formatacao da aba Funil — a regeneracao devolve nota, amarelo e tudo)
    import funil as _funil
    import funil_graficos as _fgraf
    _funil.gerar(caminho, log=log)
    _fgraf.gerar(caminho, log=log)

    falhos = sorted(d.isoformat() for d, v in manifesto.items() if v == "FALHA")
    resumo = {
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "dias": len(manifesto),
        "dias_com_ato": sum(1 for v in manifesto.values() if v == "ok"),
        "dias_vazios": sum(1 for v in manifesto.values() if v == "vazio"),
        "dias_falha": falhos,
        "atos_mec": len(atos_mec), "relevantes": len(relevantes),
        "linhas_novas": int(n_novas),
        "minutos": round((time.time() - t0) / 60, 1),
        "novidades_medicina": [],
    }
    if n_novas and len(med):
        try:
            dd = pd.to_datetime(med["data_decisao"], errors="coerce").dt.date
            rec = med[(dd >= inicio) & (dd <= fim)]
            resumo["novidades_medicina"] = [
                f"{r['tipo_decisao']}: {str(r['curso'])[:40]} — {str(r['ies'])[:50]}"
                for _, r in rec.tail(20).iterrows()]
        except Exception:
            pass
    if falhos:
        avisos.aviso(f"Varredura DOU: {len(falhos)} dia(s) NAO cobertos mesmo apos "
                     f"retentativa: {', '.join(falhos)} — rode de novo esse intervalo")
    log(f"[varredura] FIM em {resumo['minutos']} min | {n_novas} linha(s) nova(s) | "
        f"falhas: {len(falhos)}")
    return resumo


# ------------------------------------------------------------------ e-mail do relatorio
def enviar_relatorio(resumo, destinatarios, drive_url=""):
    """Relatorio da varredura por e-mail. Sem credencial (rodada local sem env) so loga."""
    user = os.environ.get("EMAIL_REMETENTE", "").strip()
    pwd = os.environ.get("EMAIL_SENHA", "").replace(" ", "").strip()
    to = [e.strip() for e in str(destinatarios or "").replace(";", ",").split(",")
          if e.strip()]
    if not (user and pwd and to):
        print("[varredura] sem credencial/destinatario de e-mail — relatorio so no log")
        return False
    import smtplib
    import ssl
    from email.message import EmailMessage
    falhas = resumo["dias_falha"]
    pref = "[INCOMPLETO] " if falhas else ""
    msg = EmailMessage()
    msg["Subject"] = (f"{pref}Varredura DOU {resumo['inicio']} a {resumo['fim']} — "
                      f"{resumo['linhas_novas']} linha(s) nova(s)")
    msg["From"], msg["To"] = user, ", ".join(to)
    linhas_med = "".join(f"<li>{m}</li>" for m in resumo["novidades_medicina"]) or \
        "<li>nenhuma novidade de Medicina no periodo</li>"
    aviso_falha = ("" if not falhas else
                   f"<p style='color:#B00020'><b>DIAS NAO COBERTOS "
                   f"(rode o periodo de novo):</b> {', '.join(falhas)}</p>")
    corpo = f"""
    <div style="font-family:Arial,sans-serif;font-size:14px">
      <h2>Varredura DOU — {resumo['inicio']} a {resumo['fim']}</h2>
      {aviso_falha}
      <table border="1" cellpadding="6" style="border-collapse:collapse">
        <tr><td>Dias varridos</td><td>{resumo['dias']}</td></tr>
        <tr><td>Dias com ato do MEC</td><td>{resumo['dias_com_ato']}</td></tr>
        <tr><td>Dias sem ato (normal)</td><td>{resumo['dias_vazios']}</td></tr>
        <tr><td><b>Dias com FALHA</b></td><td><b>{len(falhas)}</b></td></tr>
        <tr><td>Atos do MEC no periodo</td><td>{resumo['atos_mec']}</td></tr>
        <tr><td>Relevantes (regulacao)</td><td>{resumo['relevantes']}</td></tr>
        <tr><td><b>Linhas novas na base</b></td><td><b>{resumo['linhas_novas']}</b></td></tr>
        <tr><td>Duracao</td><td>{resumo['minutos']} min</td></tr>
      </table>
      <h3>Medicina no periodo</h3><ul>{linhas_med}</ul>
      {f'<p><a href="{drive_url}">Planilha no Drive</a></p>' if drive_url else ''}
      <p style="color:#666;font-size:12px">Gerado por dou_varredura.py — deterministico,
      sem IA. A aba Notas registra a cobertura deste periodo.</p>
    </div>"""
    msg.set_content(f"Varredura {resumo['inicio']}..{resumo['fim']}: "
                    f"{resumo['linhas_novas']} novas; falhas: {len(falhas)}")
    msg.add_alternative(corpo, subtype="html")
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(user, pwd)
        s.send_message(msg)
    print(f"[varredura] relatorio enviado para {', '.join(to)}")
    return True


if __name__ == "__main__":
    argv = sys.argv[1:]
    def _pega(nome, padrao=None):
        if f"--{nome}" in argv:
            i = argv.index(f"--{nome}")
            if i + 1 < len(argv):
                return argv[i + 1]
        return padrao
    inicio = _pega("inicio")
    if not inicio:
        sys.exit("uso: python dou_varredura.py --inicio AAAA-MM-DD [--fim AAAA-MM-DD] "
                 "[--arquivo x.xlsx] [--email a@b.com]")
    fim = _pega("fim")
    resumo = varrer(date.fromisoformat(inicio),
                    date.fromisoformat(fim) if fim else None,
                    _pega("arquivo", "Regulacao_Cursos.xlsx"))
    email = _pega("email")
    if email:
        enviar_relatorio(resumo, email)
    print(resumo)
