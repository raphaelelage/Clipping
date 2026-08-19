"""
fontes_extra.py — fontes complementares ao Google News, por vertical.

Tudo roda EM PARALELO e usa API/RSS (1 request por fonte) para nao pesar no GitHub Actions.
Custo tipico: ~10-20s no total.

Grupos:
  1. WP   — sites WordPress: <base>/wp-json/wp/v2/posts?after=<ISO>  (entidades do setor)
  2. RSS  — feeds proprios (JOTA, Medicina S/A, Setor Saude, Fiocruz, CADE, FNDE...)
  3. DOU  — Diario Oficial da Uniao por FRASE EXATA (in.gov.br)
  4. CVM  — fatos relevantes / comunicados ao mercado (dados abertos IPE)

Usado por clipping_core.collect(). Cada fonte diz se aplica filtro de palavra-chave:
entidades do setor publicam quase so o que interessa (filtrar=False); fontes amplas
(JOTA, CADE, DOU) filtram por keyword para nao virar ruido.
"""
from __future__ import annotations
import io
import json
import re
import zipfile
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser

ARQUIVO_RX = re.compile(r"\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?|pptx?)(/view)?$", re.I)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TIMEOUT = 20

# ------------------------------------------------------------------ catalogo por vertical
# (nome exibido, url base, filtrar_por_keyword)
WP_SITES = {
    "saude": [
        ("ANAHP", "https://www.anahp.com.br", False),
        ("Interfarma", "https://www.interfarma.org.br", False),
        ("SindHosp", "https://sindhosp.org.br", False),
        ("ABIMED", "https://abimed.org.br", False),
        ("Abifina", "https://abifina.org.br", False),
        ("ABIIS", "https://abiis.org.br", False),
        ("Cofen", "https://www.cofen.gov.br", False),
    ],
    "educacao": [
        ("Semesp", "https://www.semesp.org.br", False),
        ("ANUP", "https://anup.org.br", False),
        ("Todos Pela Educação", "https://todospelaeducacao.org.br", False),
        ("Educa Insights", "https://educa-insights.com.br", False),
    ],
}

RSS_FEEDS = {
    "saude": [
        ("Medicina S/A", "https://medicinasa.com.br/feed/", False),
        ("Setor Saúde", "https://www.setorsaude.com.br/feed/", False),
        ("Fiocruz", "https://agencia.fiocruz.br/rss-afn.xml", True),
        ("JOTA", "https://www.jota.info/feed", True),
        ("CADE", "https://www.gov.br/cade/rss.xml", True, "/noticias/"),
        ("Consumidor Moderno", "https://www.consumidormoderno.com.br/feed/", True),
    ],
    "educacao": [
        ("INEP", "https://www.gov.br/inep/rss.xml", False, "/noticias/"),
        ("JOTA", "https://www.jota.info/feed", True),
        ("CADE", "https://www.gov.br/cade/rss.xml", True, "/noticias/"),
    ],
}

# Diario Oficial da Uniao — FRASE EXATA (busca livre e OR de palavras => ruido demais)
DOU_TERMOS = {
    "saude": ["Agência Nacional de Saúde Suplementar", "saúde suplementar", "plano de saúde",
              "Hapvida", "Rede D'Or", "Dasa", "Oncoclínicas", "Qualicorp", "Hypera",
              "registro de medicamento", "Conitec"],
    "educacao": ["recredenciamento", "credenciamento de instituição", "autorização de curso",
                 "Conselho Nacional de Educação", "Fies", "Prouni", "Enem",
                 "Estácio", "Anhanguera", "Anhembi Morumbi", "Uniasselvi", "Unicesumar"],
}

# CVM — fato relevante / comunicado ao mercado das empresas cobertas
CVM_EMPRESAS = {
    "saude": ["HAPVIDA", "REDE D'OR", "REDE DOR", "DASA", "DIAGNOSTICOS DA AMERICA",
              "ONCOCLINICAS", "QUALICORP", "HYPERA", "BLAU", "VIVEO", "MATER DEI",
              "RAIA DROGASIL", "PAGUE MENOS", "DIMED", "ODONTOPREV", "FLEURY"],
    "educacao": ["COGNA", "YDUQS", "SER EDUCACIONAL", "ANIMA", "VASTA", "AFYA", "VITRU",
                 "CRUZEIRO DO SUL", "ARCO"],
}
CVM_CATEGORIAS = ("Fato Relevante", "Comunicado ao Mercado")
CVM_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"


# ------------------------------------------------------------------ coletores
def _fmt(dt, tz):
    loc = dt.astimezone(tz) if dt.tzinfo else dt
    return loc.strftime("%a, %d %b %Y"), loc.strftime("%H:%M:%S")


def _wp(nome, base, filtrar, desde, ctx):
    """WordPress REST: usa o parametro 'after' (ISO) — so traz o que e novo."""
    url = (f"{base}/wp-json/wp/v2/posts?per_page=30"
           f"&after={desde.isoformat()}T00:00:00")
    rows = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
            return rows
        for p in r.json():
            titulo = re.sub(r"<[^>]+>", "", (p.get("title") or {}).get("rendered", "")).strip()
            titulo = (titulo.replace("&#8211;", "–").replace("&#038;", "&")
                      .replace("&#8220;", "“").replace("&#8221;", "”")
                      .replace("&#8217;", "'").replace("&amp;", "&"))
            link = p.get("link", "")
            if not titulo or not link:
                continue
            kw = ctx["match"](titulo) if filtrar else nome
            if not kw:
                continue
            try:
                dt = datetime.fromisoformat(p.get("date", "")).replace(tzinfo=ctx["tz"])
            except Exception:
                dt = datetime.now(ctx["tz"])
            d, h = _fmt(dt, ctx["tz"])
            rows.append((titulo, nome, d, h, kw, link, base))
    except Exception:
        pass
    return rows


def _rss(nome, url, filtrar, cutoff, ctx, exige=None):
    rows = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        for e in feedparser.parse(r.content).entries:
            titulo = (e.get("title") or "").strip()
            link = e.get("link", "")
            if not titulo or not link or ARQUIVO_RX.search(link):
                continue
            if exige and exige not in link:      # gov.br mistura documento com noticia
                continue
            dt = ctx["to_dt"](e.get("published", e.get("updated", "")))
            if dt and dt < cutoff:
                continue
            kw = ctx["match"](titulo) if filtrar else nome
            if not kw:
                continue
            d, h = (_fmt(dt, ctx["tz"]) if dt else ("", ""))
            rows.append((titulo, nome, d, h, kw, link, url))
    except Exception:
        pass
    return rows


def _dou(termo, from_date, ctx):
    """Busca por FRASE EXATA no DOU. Devolve atos (portarias, decisoes, extratos)."""
    dias = (date.today() - from_date).days
    janela = "dia" if dias <= 1 else ("semana" if dias <= 7 else "mes")
    url = ("https://www.in.gov.br/consulta/-/buscar/dou?q=%22"
           + requests.utils.quote(termo) + f"%22&s=do1&exactDate={janela}&sortType=0")   # DO1 = atos normativos
    rows = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        m = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            return rows
        dados = json.loads(m.group(1))
        arr = dados.get("jsonArray") if isinstance(dados, dict) else dados
        for it in (arr or []):
            titulo = (it.get("title") or "").strip()
            if not titulo:
                continue
            try:
                d0 = datetime.strptime(it.get("pubDate", ""), "%d/%m/%Y").date()
            except Exception:
                d0 = None
            if d0 and d0 < from_date:
                continue
            slug = it.get("urlTitle") or ""
            link = ("https://www.in.gov.br/web/dou/-/" + slug.lstrip("/")) if slug \
                else "https://www.in.gov.br/consulta/-/buscar/dou"
            orgao = (it.get("hierarchyStr") or "").split("/")[-1].strip()
            rows.append((f"{titulo}" + (f" — {orgao}" if orgao else ""), "DOU",
                         d0.strftime("%a, %d %b %Y") if d0 else "", "",
                         f"DOU: {termo}", link, "https://www.in.gov.br"))
    except Exception:
        pass
    return rows


def _cvm(empresas, from_date, ctx):
    """Fatos relevantes / comunicados ao mercado (dados abertos da CVM)."""
    rows = []
    try:
        r = requests.get(CVM_URL.format(ano=from_date.year), headers=HEADERS, timeout=60)
        if r.status_code != 200:
            return rows
        z = zipfile.ZipFile(io.BytesIO(r.content))
        linhas = z.read(z.namelist()[0]).decode("latin-1").splitlines()
        cab = linhas[0].split(";")
        idx = {c: i for i, c in enumerate(cab)}
        alvo = [e.upper() for e in empresas]
        for ln in linhas[1:]:
            c = ln.split(";")
            if len(c) < len(cab):
                continue
            nome = c[idx["Nome_Companhia"]].upper()
            if not any(a in nome for a in alvo):
                continue
            if c[idx["Categoria"]] not in CVM_CATEGORIAS:
                continue
            try:
                d0 = datetime.strptime(c[idx["Data_Entrega"]][:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if d0 < from_date:
                continue
            assunto = (c[idx["Assunto"]] or c[idx["Categoria"]]).strip()
            titulo = f"{c[idx['Nome_Companhia']].title()} — {c[idx['Categoria']]}: {assunto}"[:180]
            rows.append((titulo, "CVM", d0.strftime("%a, %d %b %Y"), "",
                         f"CVM: {c[idx['Categoria']]}", c[idx["Link_Download"]],
                         "https://dados.cvm.gov.br"))
    except Exception:
        pass
    return rows


# ------------------------------------------------------------------ orquestrador
def coletar(vertical, cutoff, from_date, match_fn, to_dt_fn, tz, log=print):
    """Coleta de todas as fontes extras da vertical, em paralelo.
    Devolve linhas no formato COLS do clipping_core."""
    ctx = {"match": match_fn, "to_dt": to_dt_fn, "tz": tz}
    v = vertical if vertical in WP_SITES else "saude"
    tarefas = []
    for nome, base, filtrar in WP_SITES.get(v, []):
        tarefas.append((f"wp:{nome}", lambda n=nome, b=base, f=filtrar: _wp(n, b, f, from_date, ctx)))
    for item in RSS_FEEDS.get(v, []):
        nome, url, filtrar = item[0], item[1], item[2]
        exige = item[3] if len(item) > 3 else None
        tarefas.append((f"rss:{nome}",
                        lambda n=nome, u=url, f=filtrar, e=exige: _rss(n, u, f, cutoff, ctx, e)))
    for termo in DOU_TERMOS.get(v, []):
        tarefas.append((f"dou:{termo}", lambda t=termo: _dou(t, from_date, ctx)))
    if CVM_EMPRESAS.get(v):
        tarefas.append(("cvm", lambda: _cvm(CVM_EMPRESAS[v], from_date, ctx)))

    rows, por_fonte = [], {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fn): nome for nome, fn in tarefas}
        for f in as_completed(futs):
            try:
                got = f.result() or []
            except Exception:
                got = []
            if got:
                por_fonte[futs[f].split(":")[0]] = por_fonte.get(futs[f].split(":")[0], 0) + len(got)
            rows.extend(got)
    if log:
        resumo = " ".join(f"{k}={v2}" for k, v2 in sorted(por_fonte.items())) or "nada"
        log(f"[fontes_extra/{v}] {len(tarefas)} fontes -> {len(rows)} itens ({resumo})")
    return rows
