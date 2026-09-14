"""BACKFILL COMPLETO: rele TODOS os atos do DOU que ja estao na base e reclassifica.

Pedido do dono (14/09/2026): "voce precisa ler novamente os DOU, esta classificando
coisas erradas provavelmente... faca uma varredura para ver tudo isso, nao pode passar
batido" e "rode um backfill completo, estruture ele mas PRESERVE PROCESSAMENTO".

O que ele faz, por ato (7.005 atos distintos na base de hoje):
  1. baixa texto integral + HTML uma vez e GUARDA em cache (backfill_cache.parquet).
     Rodar de novo nao rebaixa nada — e isso que "preserva processamento";
  2. reclassifica o ato inteiro com o classificador atual (verbo do Art. 1, Art. 2
     quando o 1 e procedimental, aditamento separado de indeferimento de curso);
  3. reclassifica LINHA A LINHA quando o ato tem mais de um dispositivo: cada tabela
     leva o verbo do artigo que a introduz (Art. 1 extingue, Art. 3 reduz ingresso...);
  4. le a MODALIDADE do ato ("na modalidade a distancia" / presencial) e grava por linha.

O que ele NAO faz: nao apaga linha, nao mexe em cod_ies/municipio/vagas nem em nada que
veio de enriquecimento ou da mao do dono. So escreve tipo_decisao e modalidade.

Uso:
    python backfill_dou.py <arquivo.xlsx>            # diagnostico, nao grava
    python backfill_dou.py <arquivo.xlsx> --aplicar  # grava a aba Atos
"""
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import dou_extrair as dx
import dou_historico as dh

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "backfill_cache.parquet")
WORKERS = 8

RX_EAD = re.compile(r"modalidade\s+a\s+dist[âa]ncia|educa[çc][ãa]o\s+a\s+dist[âa]ncia|"
                    r"\bEaD\b|cursos?\s+EaD", re.I)
RX_PRES = re.compile(r"modalidade\s+presencial|na\s+modalidade\s+presencial", re.I)


def _nk(v):
    """Chave de linha dentro do ato: o que a tabela traz (curso ou processo)."""
    s = str(v or "").strip().lower()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _modalidade(texto):
    if RX_EAD.search(texto or ""):
        return "EAD"
    if RX_PRES.search(texto or ""):
        return "Presencial"
    return ""


def baixar(links, log=print):
    """Texto e HTML de cada ato, com cache em disco. Devolve DataFrame indexado por link."""
    ja = pd.DataFrame()
    if os.path.exists(CACHE):
        ja = pd.read_parquet(CACHE)
        log(f"[backfill] cache: {len(ja)} ato(s) ja baixados")
    faltam = [l for l in links if l not in set(ja["link"] if len(ja) else [])]
    log(f"[backfill] a baixar agora: {len(faltam)}")
    if not faltam:
        return ja
    out, t0 = [], time.time()

    def um(link):
        try:
            texto, html = dh.texto_integral(link.split("/-/")[-1])
            return {"link": link, "texto": str(texto or "")[:60000],
                    "html": str(html or "")[:400000], "erro": ""}
        except Exception as e:
            return {"link": link, "texto": "", "html": "",
                    "erro": type(e).__name__ + ": " + str(e)[:80]}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(um, l): l for l in faltam}
        for i, fut in enumerate(as_completed(futs), start=1):
            out.append(fut.result())
            if i % 250 == 0:
                falta_min = (time.time() - t0) / i * (len(faltam) - i) / 60
                log(f"[backfill] {i}/{len(faltam)} baixados | ~{falta_min:.0f} min restantes")
            if i % 1000 == 0:      # salva parcial: queda de rede nao joga fora o que veio
                pd.concat([ja, pd.DataFrame(out)], ignore_index=True).to_parquet(CACHE)
    tudo = pd.concat([ja, pd.DataFrame(out)], ignore_index=True)
    tudo.to_parquet(CACHE)
    log(f"[backfill] cache gravado: {len(tudo)} ato(s) | falhas: "
        f"{int((tudo['erro'] != '').sum())}")
    return tudo


def reclassificar(atos, cache, log=print):
    """Devolve (atos com tipo_decisao/modalidade novos, resumo das mudancas)."""
    por_link = {r.link: r for r in cache.itertuples()}
    if "modalidade" not in atos.columns:
        atos["modalidade"] = ""
    antes = atos["tipo_decisao"].astype(str).copy()
    n_ato = n_linha = n_mod = 0
    for link, g in atos.groupby(atos["link"].astype(str)):
        c = por_link.get(link)
        if c is None or not c.texto:
            continue
        titulo = str(g.iloc[0].get("ato", ""))
        tipo = dx.classificar(titulo, c.texto)
        mod = _modalidade(c.texto)
        # tipo por DISPOSITIVO: cada tabela leva o verbo do artigo que a introduz
        tipo_por_chave = {}
        if c.html:
            try:
                for linha in dx.linhas_da_tabela(c.html):
                    t = dx.tipo_da_tabela(linha.get("_contexto", ""))
                    if not t:
                        continue
                    for campo in ("curso", "processo_emec"):
                        if str(linha.get(campo, "")).strip():
                            tipo_por_chave[_nk(linha[campo])] = t
            except Exception:
                pass
        for i in g.index:
            novo = tipo
            for col in ("curso", "processo"):
                k = _nk(atos.at[i, col] if col in atos.columns else "")
                if k and k in tipo_por_chave:
                    novo = tipo_por_chave[k]
                    n_linha += 1
                    break
            if novo != str(atos.at[i, "tipo_decisao"]):
                atos.at[i, "tipo_decisao"] = novo
                n_ato += 1
            if mod and not str(atos.at[i, "modalidade"] or "").strip():
                atos.at[i, "modalidade"] = mod
                n_mod += 1
    dif = pd.crosstab(antes[antes != atos["tipo_decisao"].astype(str)],
                      atos["tipo_decisao"].astype(str)[antes != atos["tipo_decisao"]])
    log(f"[backfill] tipo_decisao alterado em {n_ato} linha(s) "
        f"({n_linha} pelo dispositivo da propria tabela) | modalidade preenchida em "
        f"{n_mod}")
    if len(dif):
        log("[backfill] de -> para:\n" + dif.to_string())
    log("[backfill] modalidade: "
        + str(atos["modalidade"].astype(str).value_counts().to_dict()))
    return atos


def main(caminho, aplicar=False, log=print):
    xl = pd.ExcelFile(caminho)
    atos = xl.parse("Atos")
    links = sorted({str(x) for x in atos["link"] if str(x).startswith("http")})
    log(f"[backfill] {len(atos)} linhas | {len(links)} atos distintos")
    cache = baixar(links, log)
    atos = reclassificar(atos, cache, log)
    if not aplicar:
        log("[backfill] diagnostico apenas — rode com --aplicar para gravar")
        return atos
    outras = {n: xl.parse(n) for n in xl.sheet_names
              if n not in ("Atos", "Funil", "Graficos", "Graf_Dados", "Conferir")}
    with pd.ExcelWriter(caminho, engine="openpyxl", date_format="DD/MM/YYYY",
                        datetime_format="DD/MM/YYYY") as xw:
        atos.to_excel(xw, sheet_name="Atos", index=False)
        for n, d in outras.items():
            d.to_excel(xw, sheet_name=n, index=False)
        for w in xw.book.worksheets:
            w.freeze_panes = "A2"
            w.auto_filter.ref = w.dimensions
    log(f"[backfill] gravado em {caminho}. AGORA: python funil.py {caminho} "
        f"(e funil_graficos) para o Funil refletir a reclassificacao.")
    return atos


if __name__ == "__main__":
    arq = sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx"
    main(arq, aplicar="--aplicar" in sys.argv)
