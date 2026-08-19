"""
clipping_core.py — coleta multi-fonte de noticias (saude + educacao BR) para equity research.

Fontes (todas funcionam de IP de datacenter / GitHub Actions):
  1. Google News (pygooglenews) por keyword, filtrado por whitelist de fontes
  2. Brazil Stock Guide via Google News (site-search) + sitemap (suplemento, com backoff)
  3. ANS / Anvisa (portais gov.br)
  4. Valor Economico via RSS (pox.globo.com) — sem login/paywall

Janela de tempo aceita horas e dias: "1h", "6h", "12h", "1d", "3d", "7d".

Uso:
    from clipping_core import collect, build_email_html, build_csv_bytes, send_email
    df = collect("1d")
"""
from __future__ import annotations
import re, unicodedata, os, time, random, io, smtplib
from datetime import datetime, timedelta, timezone, date
from email.utils import parsedate_to_datetime
from email.message import EmailMessage
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests, feedparser
import pandas as pd
from bs4 import BeautifulSoup
from pygooglenews import GoogleNews
from googlenewsdecoder import gnewsdecoder
import fontes_extra

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = timezone(timedelta(hours=-3))

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

COLS = ["title", "source", "date", "hour", "searched_keyword", "link", "source_link"]

def _load_list(path, default):
    """Le uma lista de um arquivo (1 item por linha, # = comentario). Editavel pelo app.
    Cai no 'default' se o arquivo nao existir ou estiver vazio."""
    try:
        if os.path.exists(path):
            lines = [l.strip() for l in open(path, encoding="utf-8").read().splitlines()
                     if l.strip() and not l.lstrip().startswith("#")]
            if lines:
                return lines
    except Exception:
        pass
    return default

# ----------------------------------------------------------------------------- keywords
DEFAULT_KEYWORDS = [
    "saúde", "saúde suplementar", "educação", "ensino", "agencia nacional de saude",
    "ans", "anahp", "anvisa", "athena saude", "amil", "mec", "fenasaude", "abramge",
    "elfa", "caged", "Rede D'Or", "Rede DOr", "Hapvida", "NDI", "Fleury",
    "Diagnosticos da America", "Dasa", "Panvel", "Pague Menos", "Pardini", "Odontoprev",
    "Kora", "CM Hospitalar", "Viveo", "Mater Dei", "Qualicorp", "Sulamerica", "Hypera",
    "Blau", "Raia", "Oncoclinicas", "Dimed", "Cogna", "YDUQS", "Ânima", "Ser Educacional",
    "Cruzeiro do Sul", "Afya", "Vitru", "Vasta Educação", "Arco Educação", "medicamentos",
    "farmaceutica", "farmacia", "cimed", "genericos", "canetas emagrecedoras", "emagrecedor",
    "GLP-1", "EMS", "prevent senior", "mais medicos", "ead", "ensino tecnico", "icms",
    "beneficio fiscal", "IRPJ", "hospital", "hospitais", "operadoras", "planos de saúde",
    "faculdade", "pnld", "glosa", "autismo", "oncologia", "cancer", "sinistralidade",
    "sinistro", "alfapoetina", "medicina", "pravaler", "PIS", "COFINS", "Medida provisória",
    "Mercado Livre", "Block Trade", "Bradesco Saude", "Dr. Consulta",
]
keywords = _load_list("keywords.txt", DEFAULT_KEYWORDS)   # editavel pelo app (setado por set_vertical)

DEFAULT_WHITELIST = [
    "GOV.BR", "CNN Brasil", "Senado Federal", "Cofen", "ConJur", "Agência Brasil", "UOL",
    "O Globo", "Poder360", "G1", "UOL Educação", "VEJA", "Secretaria da Educação",
    "Governo do Estado de São Paulo", "Terra", "InfoMoney", "UOL Confere", "Metrópoles",
    "Exame", "Exame Notícias", "Valor Econômico", "Gazeta do Povo", "Seu Dinheiro",
    "Estadão", "Extra", "Fenacor", "JOTA Info", "UOL Economia", "Valor Investe",
    "Globo.com", "OLiberal.com", "SpaceMoney", "Rede D'Or São Luiz", "Medicina S/A",
    "Finance News", "E-Investidor", "br.ADVFN.com", "saudebusiness.com", "Investnews",
    "Setor Saúde", "Pipeline", "Money Times", "Istoé Dinheiro", "Época NEGÓCIOS",
    "Investing.com Brasil", "Suno Notícias", "Guia da Farmácia", "Brazil Journal",
    "NeoFeed", "Vogue Brasil", "VEJA São Paulo", "ISTOÉ", "R7.com", "Acionista.com.br",
    "Globo", "BM&C NEWS", "Forbes Brasil", "Revista Oeste", "Revista Fórum", "Governo",
    "Contábeis", "Brasil 61", "Canal Autismo / Revista Autismo", "Congresso em Foco",
    "Consumidor Moderno", "Correio Braziliense", "O Tempo", "Portal de Fusões e Aquisições",
    "Portal Farmacêutico", "Política Estadão", "Portal Panorama Farmacêutico",
    "Panorama Farmacêutico", "Revista Apólice", "Futuro da Saúde", "Migalhas",
    "Bloomberg.com", "Bloomberg Linea Brasil", "Folha de S.Paulo", "pipelinevalor",
]
WHITELIST = _load_list("sources.txt", DEFAULT_WHITELIST)   # editavel pelo app (setado por set_vertical)

# ----------------------------------------------------------------------------- verticais
# Cada vertical tem seus proprios arquivos (editaveis pelo app) e portais gov.br.
# govbr: (site, nome exibido, caminhos conhecidos da secao de noticias)
VERTICAIS = {
    "saude": {
        "label": "Saúde",
        "govbr": [
            ("ans", "ANS", ("https://www.gov.br/ans/pt-br/assuntos/noticias-1",
                            "https://www.gov.br/ans/pt-br/assuntos/noticias")),
            ("anvisa", "Anvisa", ("https://www.gov.br/anvisa/pt-br/assuntos/noticias-anvisa",)),
        ],
    },
    "educacao": {
        "label": "Educação",
        "govbr": [
            ("mec", "MEC", ("https://www.gov.br/mec/pt-br/assuntos/noticias",)),
            ("capes", "Capes", ("https://www.gov.br/capes/pt-br/assuntos/noticias",)),
        ],
    },
}
# vertical combinada = uniao das duas (mesmos portais, sem repetir)
_vistos = set()
VERTICAIS["saude_educacao"] = {
    "label": "Saúde e Educação",
    "govbr": [g for g in VERTICAIS["saude"]["govbr"] + VERTICAIS["educacao"]["govbr"]
              if not (g[0] in _vistos or _vistos.add(g[0]))],
}
del _vistos
VERTICAL = "saude"

# A vertical combinada NAO tem listas proprias: elas sao a uniao das outras duas,
# calculada a cada execucao. Assim editar Saude ou Educacao ja reflete na combinada.
COMBINADA = "saude_educacao"
PARTES = ("saude", "educacao")

def arquivos_vertical(vertical):
    """Arquivos de configuracao da vertical. Na combinada, keywords/sources sao None
    (derivados) — so o prompt e proprio."""
    v = vertical if vertical in VERTICAIS else "saude"
    if v == COMBINADA:
        return {"keywords": None, "sources": None, "prompt": f"ai_prompt_{v}.txt"}
    return {"keywords": f"keywords_{v}.txt", "sources": f"sources_{v}.txt",
            "prompt": f"ai_prompt_{v}.txt"}

def _dedup(itens):
    vis, out = set(), []
    for x in itens:
        k = _norm(x)
        if k and k not in vis:
            vis.add(k); out.append(x)
    return out

def set_vertical(vertical):
    """Aponta a coleta para uma vertical: recarrega keywords e fontes dos arquivos dela.
    Na combinada, usa a UNIAO das listas de saude e educacao (sem arquivo proprio).
    Fallback: arquivos antigos sem sufixo (keywords.txt/sources.txt) e depois os defaults."""
    global VERTICAL, keywords, WHITELIST
    VERTICAL = vertical if vertical in VERTICAIS else "saude"
    if VERTICAL == COMBINADA:
        kw, src = [], []
        for parte in PARTES:
            f2 = arquivos_vertical(parte)
            kw += _load_list(f2["keywords"], [])
            src += _load_list(f2["sources"], [])
        keywords = _dedup(kw) or DEFAULT_KEYWORDS
        WHITELIST = _dedup(src) or DEFAULT_WHITELIST
        return VERTICAL
    f = arquivos_vertical(VERTICAL)
    base_kw = DEFAULT_KEYWORDS if VERTICAL == "saude" else []
    base_src = DEFAULT_WHITELIST
    keywords = _load_list(f["keywords"], _load_list("keywords.txt", base_kw)
                          if VERTICAL == "saude" else base_kw)
    WHITELIST = _load_list(f["sources"], _load_list("sources.txt", base_src)
                           if VERTICAL == "saude" else base_src)
    return VERTICAL

VALOR_FEEDS = [
    "https://pox.globo.com/rss/valor/", "https://pox.globo.com/rss/valor/financas/",
    "https://pox.globo.com/rss/valor/empresas/", "https://pox.globo.com/rss/valor/brasil/",
    "https://pox.globo.com/rss/valor/politica/", "https://pox.globo.com/rss/valor/impresso/",
    "https://pox.globo.com/rss/valor/opiniao/",
]

BSG_ART = re.compile(
    r"brazilstockguide\.com/(?:br/)?(?:insights|behind-the-lines|wake-up-call|opinion)(?:-br)?/[a-z0-9][a-z0-9-]+/?$")

# classificacao p/ o e-mail
EDU_KW = ["educacao", "mec", "ensino", "ead", "pnld", "faculdade", "cogna", "yduqs", "anima",
          "ser educacional", "cruzeiro do sul", "afya", "vitru", "vasta", "arco", "pravaler",
          "enem", "enade", "enamed", "professor", "escola", "universidade", "aluno", "sisu",
          "fundeb", "capes", "docente"]
NOISE_KW = ["novela", "selecao", "neymar", "futebol", "jogador", "bbb", "horoscopo", "signo",
            "libertadores", "copa do mundo", "mega-sena", "loteria", "festa junina", "festas juninas",
            "celebridade", "claudia raia", "simony", "ex-paquito", "whitney", "obama", "biancardi",
            "novelas", "reality", "bbb ", "viraliza", "look", "show de", "cinema", "reboot"]

# ----------------------------------------------------------------------------- helpers
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()

def match_keywords(title: str):
    t = _norm(title)
    for kw in keywords:
        if re.search(r"\b" + re.escape(_norm(kw)) + r"(es|s)?\b", t):
            return kw
    return None

def to_dt(s):
    """parse RFC822 ou ISO8601 -> datetime aware (UTC se sem tz). None se falhar."""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _fmt(dt):
    if not dt:
        return "", ""
    loc = dt.astimezone(TZ)
    return loc.strftime("%a, %d %b %Y"), loc.strftime("%H:%M:%S")

def parse_pub(published):
    """published (RFC822/ISO) -> (date_str, hour_str, date) — usado pelo Google News."""
    dt = to_dt(published)
    d, h = _fmt(dt)
    return d, h, (dt.date() if dt else None)

def parse_period(period: str) -> timedelta:
    m = re.fullmatch(r"\s*(\d+)\s*([hHdD])\s*", str(period or "1d"))
    if not m:
        return timedelta(days=1)
    n, unit = int(m.group(1)), m.group(2).lower()
    return timedelta(hours=n) if unit == "h" else timedelta(days=n)

def fetch_resilient(url, tries=3, base=2, timeout=15, jina=True):
    """GET com backoff em 429. Se o host esta FORA (erro de conexao), desiste rapido —
    insistir so queima tempo do Actions (ja custou 15 min num dia em que o gov.br caiu)."""
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:
                time.sleep(base * (2 ** i) + random.uniform(0, 1)); continue
            return None
        except Exception:
            if i:                      # 2 falhas de conexao seguidas -> host fora, desiste
                return None
            time.sleep(1)
    if jina:
        try:
            r = requests.get("https://r.jina.ai/" + url, timeout=25)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
    return None

# ----------------------------------------------------------------------------- coletores
def _google_news(when):
    # SEQUENCIAL com uma unica instancia (igual ao codigo antigo que pegava ~200).
    # O Google News, do IP do GitHub Actions, bloqueia rajadas concorrentes e devolve vazio —
    # por isso NAO usar ThreadPoolExecutor aqui. Operador nativo 'when:' (when:1d, when:12h, when:1h).
    gn = GoogleNews(lang="pt", country="BR")
    rows, empties = [], []

    def _fetch(kw):
        try:
            return gn.search(kw, when=when).get("entries", [])
        except Exception:
            return None

    def _add(kw, entries):
        for it in entries:
            d, h, _ = parse_pub(it.get("published"))
            rows.append((it.title, it.source["title"], d, h, kw, it.link, it.source["href"]))

    # passe 1
    for kw in keywords:
        e = _fetch(kw)
        if e:
            _add(kw, e)
        else:
            empties.append(kw)   # vazio ou erro -> tenta de novo no passe 2
        time.sleep(0.5)          # pausa p/ nao tomar throttle do Google (IP do GitHub)
    # passe 2 — re-tenta so as que voltaram vazias (recupera throttle pontual)
    recovered = 0
    for kw in empties:
        time.sleep(1.0)
        e = _fetch(kw)
        if e:
            _add(kw, e); recovered += 1
    print(f"[google_news] {len(keywords)-len(empties)}/{len(keywords)} no passe 1, "
          f"+{recovered} recuperadas no retry, {len(rows)} itens brutos", flush=True)
    df = pd.DataFrame(rows, columns=COLS)
    df = df[df["source"].isin(WHITELIST)].reset_index(drop=True)

    # Brazil Stock Guide via Google News (EN + PT) — tambem sequencial
    bsg = []
    for lang in ("en", "pt"):
        try:
            g = GoogleNews(lang=lang, country="BR")
            res = g.search("site:brazilstockguide.com", when=when)
            for e in res.get("entries", []):
                if e.get("source", {}).get("title") != "Brazil Stock Guide":
                    continue
                title = e["title"].replace(" - Brazil Stock Guide", "").strip()
                kw = match_keywords(title)
                if kw:
                    d, h, _ = parse_pub(e.get("published", ""))
                    bsg.append((title, "Brazil Stock Guide", d, h, kw, e["link"],
                                "https://brazilstockguide.com"))
        except Exception:
            pass
    if bsg:
        df = pd.concat([df, pd.DataFrame(bsg, columns=COLS)], ignore_index=True)
    return df, len(bsg)

_DATE_RX = re.compile(r"(?:Publicado|Atualizado)\s+em\s+(\d{2}/\d{2}/\d{4})", re.I)
_MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
          "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
_URL_MES_RX = re.compile(r"/(\d{4})/([a-zç]+)/", re.I)

def _url_ano_mes(link):
    """(ano, mes) a partir de URLs tipo .../noticias/2026/agosto/slug — usado p/ descartar
    artigos antigos SEM precisar abrir a pagina (o template do MEC nao mostra data)."""
    m = _URL_MES_RX.search(link or "")
    if m:
        mes = _MESES.get(_norm(m.group(2)))
        if mes:
            return int(m.group(1)), mes
    return None

def _govbr_article_meta(url):
    """(data, titulo) de uma noticia gov.br cujo tile nao mostra data (template MEC).
    Le 'Publicado em DD/MM/AAAA' (ou 'Atualizado em') e og:title/h1 na pagina do artigo."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.content, "html.parser")
        d = None
        m = _DATE_RX.search(soup.get_text(" ", strip=True))
        if m:
            d = datetime.strptime(m.group(1), "%d/%m/%Y").date()
        og = soup.find("meta", property="og:title")
        t = (og.get("content").strip() if og and og.get("content")
             else (soup.h1.get_text(strip=True) if soup.h1 else ""))
        return d, t
    except Exception:
        return None, ""

def _govbr_article_date(url):
    return _govbr_article_meta(url)[0]

def _parse_govbr_items(soup):
    """Extrai (titulo, link, data|None) dos dois templates de listagem gov.br:
       A) '.listagem-noticias-com-foto li'  -> h2.titulo a + span.data   (ANS, Capes)
       B) 'article.tileItem'                -> h2.tileHeadline a, SEM data (MEC)"""
    out = []
    for li in soup.select(".listagem-noticias-com-foto li"):
        a, dt = li.select_one("h2.titulo a"), li.select_one("span.data")
        if not a or not dt:
            continue
        try:
            d = datetime.strptime(dt.get_text(strip=True), "%d/%m/%Y").date()
        except Exception:
            d = None
        out.append((a.get_text(strip=True), a.get("href", ""), d))
    if out:
        return out
    for art in soup.select("article.tileItem"):
        a = art.select_one("h2.tileHeadline a") or art.select_one("h2 a")
        if a and a.get("href"):
            out.append((a.get_text(strip=True), a["href"], None))   # data vem do artigo
    return out

def _scrape_govbr(base_url, source_name, from_date, max_pages=4, max_lookups=40):
    """Listagem HTML de noticias gov.br (Plone). Paginacao dinamica via b_start.
    Retorna (rows, alive): alive=True se a listagem tinha itens (mesmo que fora da janela) —
    distingue 'secao viva sem noticia recente' de 'URL morta/redirecionada' (ex.: ANS antiga
    redirecionava p/ login com HTTP 200 e voltava 0 itens em silencio).
    Se o template nao traz data (MEC), busca a data na pagina do artigo; a listagem e
    cronologica, entao para depois de algumas seguidas fora da janela."""
    rows, offset, stop, alive, lookups = [], 0, False, False, 0
    janela_ym = (from_date.year, from_date.month)
    for p in range(max_pages):
        if stop:
            break
        url = base_url if offset == 0 else f"{base_url}?b_start:int={offset}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            items = _parse_govbr_items(BeautifulSoup(r.content, "html.parser"))
            page_count = len(items)
            com_data = [(t, l, d) for t, l, d in items if d is not None]
            # template sem data (MEC): descarta pelo ano/mes da URL e busca o resto em paralelo
            sem_data = [(t, l) for t, l, d in items if d is None
                        and (_url_ano_mes(l) or janela_ym) >= janela_ym]
            sem_data = sem_data[:max(0, max_lookups - lookups)]
            if sem_data:
                lookups += len(sem_data)
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {ex.submit(_govbr_article_date, l): (t, l) for t, l in sem_data}
                    for f in as_completed(futs):
                        t, l = futs[f]
                        d = f.result()
                        if d:
                            com_data.append((t, l, d))
            recent = False
            for title, link, d in com_data:
                if d >= from_date:
                    recent = True
                    rows.append((title, source_name, d.strftime("%a, %d %b %Y"), "",
                                 f"{source_name} (portal)", link, base_url))
            if page_count == 0:
                if p == 0:
                    print(f"[{source_name}] listagem vazia em {base_url} "
                          f"(URL pode ter mudado/redirecionado)", flush=True)
                break
            alive = True
            offset += page_count
            if not recent:
                stop = True
        except Exception as e:
            print(f"[{source_name}] erro: {e}", flush=True)
            break
    return rows, alive

def _govbr_api_news(site, source_name, from_date, lang="pt-br", page=50, max_pages=4):
    """Plone REST API na RAIZ do site gov.br (@search por News Item) — INDEPENDE do caminho
    da secao de noticias, entao sobrevive a renomeacoes (/noticias -> /noticias-1 etc.).
    Retorna lista de rows, ou None se o site nao expoe a API."""
    hdrs = {**HEADERS, "Accept": "application/json"}
    base = (f"https://www.gov.br/{site}/++api++/{lang}/@search"
            f"?portal_type=News+Item&sort_on=effective&sort_order=descending&b_size={page}")
    rows = []
    for p in range(max_pages):
        try:
            r = requests.get(f"{base}&b_start={p*page}", headers=hdrs, timeout=15)
            if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                return None if p == 0 else rows
            items = r.json().get("items", [])
        except Exception:
            return None if p == 0 else rows
        if not items:
            break
        older = False
        for it in items:
            dt = to_dt(it.get("effective") or it.get("created"))
            if not dt:
                continue
            if dt.date() < from_date:
                older = True
                break
            title = (it.get("title") or "").strip()
            link = it.get("@id") or ""
            if title and link:
                ds, hs = _fmt(dt)
                rows.append((title, source_name, ds, hs, f"{source_name} (portal)",
                             link, f"https://www.gov.br/{site}/{lang}"))
        if older:
            break
    return rows

_SECAO_RX = re.compile(r"^(https?://[^\s]*?/[^/\s]*notici[^/\s]*)/", re.I)

def _secao_de(url):
    """URL de artigo -> URL da secao. Ex.: .../assuntos/noticias-1/periodo-eleitoral/slug
    -> .../assuntos/noticias-1 . Funciona qualquer que seja o nome da secao."""
    m = _SECAO_RX.match(url or "")
    return m.group(1) if m else None

def _govbr_sitemap_locs(site, max_sub=6):
    """URLs do sitemap.xml do site gov.br (segue sitemapindex). Canal de descoberta que
    sobrevive a home renderizada por JS (caso do MEC) e a renomeacao da secao."""
    txt = fetch_resilient(f"https://www.gov.br/{site}/sitemap.xml", jina=False)
    if not txt:
        return []
    locs = re.findall(r"<loc>(.*?)</loc>", txt)
    if "<sitemapindex" in txt:
        out = []
        for sm in [l for l in locs if l.endswith(".xml")][:max_sub]:
            t2 = fetch_resilient(sm, jina=False)
            if t2:
                out += re.findall(r"<loc>(.*?)</loc>", t2)
        return out
    return locs

def _discover_govbr_news_url(site, lang="pt-br"):
    """Descobre a secao de noticias sem depender de caminho fixo:
    1) link no menu da home; 2) secao mais frequente entre as URLs do sitemap.xml."""
    base = f"https://www.gov.br/{site}/{lang}"
    try:
        from urllib.parse import urljoin
        r = requests.get(base, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.content, "html.parser")
        fallback = None
        for a in soup.find_all("a", href=True):
            h = urljoin(base, a["href"])          # href pode vir relativo (/anvisa/...)
            if not re.search(r"/assuntos/noticias[^/]*/?$", h):
                continue
            if "notici" in _norm(a.get_text(strip=True)):
                return h.rstrip("/")
            fallback = fallback or h.rstrip("/")
        if fallback:
            return fallback
    except Exception:
        pass
    try:
        from collections import Counter
        secoes = Counter(s for s in (_secao_de(u) for u in _govbr_sitemap_locs(site)) if s)
        if secoes:
            return secoes.most_common(1)[0][0]
    except Exception:
        pass
    return None

def _govbr_sitemap_news(site, source_name, from_date, limite=60):
    """Ultimo recurso: coleta as noticias direto do sitemap.xml (nao depende de listagem
    nem de API). Descarta pelo ano/mes da URL e busca as datas em paralelo."""
    locs = [u for u in _govbr_sitemap_locs(site) if _secao_de(u)]
    if not locs:
        return []
    ym = (from_date.year, from_date.month)
    cand = [u for u in locs if (_url_ano_mes(u) or ym) >= ym][:limite]
    rows = []
    if not cand:
        return rows
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_govbr_article_meta, u): u for u in cand}
        for f in as_completed(futs):
            u = futs[f]
            d, t = f.result()
            if d and d >= from_date:
                if not t:
                    t = u.rstrip("/").split("/")[-1].replace("-", " ").capitalize()
                rows.append((t, source_name, d.strftime("%a, %d %b %Y"), "",
                             f"{source_name} (sitemap)", u, f"https://www.gov.br/{site}"))
    return rows

def _scrape_govbr_auto(site, source_name, from_date, known_paths=(), budget=75):
    """Coleta noticias de um site gov.br SOBREVIVENDO a mudanca de endereco da secao:
    1) API REST na raiz (independe do caminho)   2) descoberta do link no menu
    3) caminhos conhecidos. So avisa alto se NENHUM metodo achar a secao."""
    fim = time.monotonic() + budget          # teto de tempo p/ este portal
    rows = _govbr_api_news(site, source_name, from_date)
    if rows is not None:
        return rows
    if time.monotonic() > fim:
        print(f"[{source_name}] tempo esgotado (portal lento/fora) — seguindo", flush=True)
        return []
    urls = []
    disc = _discover_govbr_news_url(site)
    if disc:
        urls.append(disc)
    urls += [u for u in known_paths if u not in urls]
    for u in urls:
        if time.monotonic() > fim:
            print(f"[{source_name}] tempo esgotado (portal lento/fora) — seguindo", flush=True)
            return []
        got, alive = _scrape_govbr(u, source_name, from_date)
        if got or alive:      # achou a secao (mesmo sem noticia na janela) -> confia
            return got
    if time.monotonic() > fim:
        print(f"[{source_name}] tempo esgotado (portal lento/fora) — seguindo", flush=True)
        return []
    got = _govbr_sitemap_news(site, source_name, from_date)   # ultimo recurso
    if got:
        print(f"[{source_name}] coletado via sitemap.xml ({len(got)} noticias) — "
              f"a listagem mudou de endereco", flush=True)
        return got
    print(f"[{source_name}] AVISO: secao de noticias nao encontrada por nenhum metodo "
          f"(API raiz / menu / sitemap / caminhos conhecidos) — VERIFICAR O PORTAL", flush=True)
    return []

def _scrape_valor_rss(cutoff):
    rows, seen = [], set()
    for url in VALOR_FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            feed = feedparser.parse(r.content)
            for e in feed.entries:
                link = e.get("link", "")
                if not link or link in seen:
                    continue
                dt = to_dt(e.get("published", e.get("updated", "")))
                if dt and dt < cutoff:
                    continue
                title = e.get("title", "")
                kw = match_keywords(title)
                if kw:
                    seen.add(link)
                    d, h = _fmt(dt)
                    rows.append((title, "Valor Econômico (RSS)", d, h, kw, link,
                                 "https://valor.globo.com"))
        except Exception:
            pass
    return rows

def _bsg_title(url, slug):
    txt = fetch_resilient(url)
    if txt:
        s = BeautifulSoup(txt, "html.parser")
        og = s.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()
        if s.title:
            return s.title.get_text(strip=True).split("|")[0].strip()
    return slug.replace("-", " ").title()

def _scrape_bsg_sitemap(cutoff):
    idx = fetch_resilient("https://brazilstockguide.com/sitemap.xml")
    if not idx:
        return []
    subs = [u for u in re.findall(r"<loc>(.*?)</loc>", idx)
            if u.endswith(".xml") and "image" not in u and "video" not in u]
    entries = []
    for sm in subs:
        t = fetch_resilient(sm)
        if not t:
            continue
        for block in re.findall(r"<url>(.*?)</url>", t, re.S):
            loc = re.search(r"<loc>(.*?)</loc>", block)
            lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
            if loc:
                entries.append((loc.group(1), lm.group(1) if lm else ""))
    rows = []
    for url, lm in entries:
        if not BSG_ART.search(url):
            continue
        dt = to_dt(lm)
        if dt and dt < cutoff:
            continue
        slug = url.rstrip("/").split("/")[-1]
        kw = match_keywords(slug.replace("-", " "))
        if not kw:
            continue
        d, h = _fmt(dt)
        src = "Brazil Stock Guide (PT)" if "/br/" in url else "Brazil Stock Guide"
        rows.append((_bsg_title(url, slug), src, d, h, kw, url, "https://brazilstockguide.com"))
    rows.sort(key=lambda r: 0 if "(PT)" in r[1] else 1)
    dedup, seen = [], set()
    for r in rows:
        key = (r[4], r[2])
        if r[2] and key in seen:
            continue
        if r[2]:
            seen.add(key)
        dedup.append(r)
    return dedup

def _decode_links(df):
    mask = df["link"].astype(str).str.contains("news.google.com")
    uniq = list(set(df.loc[mask, "link"]))
    if not uniq:
        return df
    def dec(l):
        try:
            r = gnewsdecoder(l, interval=0)
            return l, (r["decoded_url"] if r.get("status") else l)
        except Exception:
            return l, l
    dmap = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(dec, l) for l in uniq]):
            o, n = f.result(); dmap[o] = n
    df["link"] = df["link"].map(lambda l: dmap.get(l, l))
    return df

# ----------------------------------------------------------------------------- orquestrador
def collect(period: str = "1d", progress=None, vertical: str | None = None) -> pd.DataFrame:
    """Coleta de todas as fontes dentro da janela `period` ('1h','3d',...), para a `vertical`
    ('saude' ou 'educacao'). Retorna DataFrame."""
    def _p(msg):
        if progress:
            progress(msg)
    if vertical:
        set_vertical(vertical)
    v = VERTICAIS.get(VERTICAL, VERTICAIS["saude"])
    when = (period or "1d").strip()
    delta = parse_period(when)
    now = datetime.now(TZ)
    cutoff = now - delta
    from_date = cutoff.date()

    if not keywords:
        print(f"[{VERTICAL}] AVISO: nenhuma palavra-chave configurada "
              f"({arquivos_vertical(VERTICAL)['keywords']}) — Google News nao sera consultado.",
              flush=True)
        df_gn = pd.DataFrame([], columns=COLS)
    else:
        _p("Google News + Brazil Stock Guide…")
        df_gn, _ = _google_news(when)   # operador 'when:' nativo (abrangente, suporta horas)

    # portais gov.br da vertical, "a prova de mudanca de endereco":
    # API raiz -> descoberta no menu -> caminhos conhecidos
    portais = []
    for site, nome, paths in v["govbr"]:
        _p(f"{nome}…")
        portais.append(_scrape_govbr_auto(site, nome, from_date, paths))

    _p("Valor (RSS)…")
    valor = _scrape_valor_rss(cutoff) if keywords else []

    _p("Brazil Stock Guide (sitemap)…")
    bsg = _scrape_bsg_sitemap(cutoff) if keywords else []

    # fontes complementares da vertical (entidades, DOU, CVM, RSS proprios) — tudo em paralelo
    _p("Fontes complementares (entidades, DOU, CVM)…")
    try:
        extras = fontes_extra.coletar(VERTICAL, cutoff, from_date, match_keywords, to_dt, TZ,
                                      log=lambda m: print(m, flush=True), norm_fn=_norm)
    except Exception as e:
        print(f"[fontes_extra] erro: {e}", flush=True)
        extras = []

    frames = [df_gn] + [pd.DataFrame(r, columns=COLS)
                        for r in (portais + [valor, bsg, extras]) if r]
    allnews = pd.concat(frames, ignore_index=True)
    if allnews.empty:
        _p("Nenhuma notícia no período.")
        return pd.DataFrame([], columns=["title", "count_news", "link", "source", "date",
                                         "hour", "searched_keyword", "source_link",
                                         "markdown", "setor"])

    allnews["_t"] = allnews["title"].map(_norm)
    allnews["count_news"] = allnews.groupby("_t")["title"].transform("size")
    allnews = allnews.drop_duplicates(subset="_t").reset_index(drop=True)

    _p("Decodificando links do Google News…")
    allnews = _decode_links(allnews)
    allnews = allnews.drop_duplicates(subset="link").reset_index(drop=True)

    allnews["markdown"] = "[" + allnews["title"].astype(str) + "](" + allnews["link"].astype(str) + ")"
    allnews["setor"] = allnews.apply(_classify, axis=1)
    allnews = allnews[["title", "count_news", "link", "source", "date", "hour",
                       "searched_keyword", "source_link", "markdown", "setor"]]
    _p(f"Pronto: {len(allnews)} notícias.")
    return allnews

# ----------------------------------------------------------------------------- classificacao / e-mail
def _classify(row) -> str:
    s = _norm(str(row["searched_keyword"]) + " " + str(row["title"]))
    if any(k in s for k in EDU_KW):
        return "educacao"
    return "saude"

def _is_noise(title) -> bool:
    n = _norm(title)
    return any(k in n for k in NOISE_KW)

def build_email_html(df: pd.DataFrame, period: str) -> str:
    clean = df[~df["title"].map(_is_noise)].copy()
    saude = clean[clean["setor"] == "saude"].sort_values("count_news", ascending=False)
    edu = clean[clean["setor"] == "educacao"].sort_values("count_news", ascending=False)

    def section(title, sub):
        if sub.empty:
            return f"<h2 style='color:#CC092F;margin:18px 0 6px'>{title}</h2><p style='color:#888'>—</p>"
        lis = "".join(
            f"<li style='margin:6px 0;line-height:1.35'>"
            f"<a href='{r.link}' style='color:#0a3d62;text-decoration:none'>{_esc(r.title)}</a>"
            f" <span style='color:#999;font-size:12px'>— {_esc(r.source)}</span></li>"
            for r in sub.itertuples()
        )
        return f"<h2 style='color:#CC092F;margin:18px 0 6px'>{title} ({len(sub)})</h2><ul style='padding-left:18px;margin:0'>{lis}</ul>"

    today = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:auto;color:#222">
      <h1 style="font-size:20px;margin:0 0 2px">Clipping — Saúde &amp; Educação</h1>
      <p style="color:#888;margin:0 0 8px;font-size:13px">Período: {period} · gerado em {today} · {len(clean)} notícias</p>
      {section("HEALTHCARE", saude)}
      {section("EDUCAÇÃO", edu)}
      <p style="color:#aaa;font-size:11px;margin-top:20px">CSV completo em anexo. Gerado automaticamente.</p>
    </div>"""

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def build_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")

def send_email(html, csv_bytes, recipients, smtp_user, smtp_pass,
               sender=None, subject=None, host="smtp.gmail.com", port=587):
    """Envia o digest HTML + CSV anexo para os destinatarios via SMTP (Gmail por padrao)."""
    recipients = [r.strip() for r in (recipients if isinstance(recipients, list)
                  else re.split(r"[,;\s]+", recipients)) if r and "@" in r]
    if not recipients:
        raise ValueError("Nenhum e-mail valido informado.")
    msg = EmailMessage()
    msg["Subject"] = subject or f"Clipping Saúde & Educação — {datetime.now(TZ).strftime('%d/%m/%Y')}"
    msg["From"] = sender or smtp_user
    msg["To"] = ", ".join(recipients)
    msg.set_content("Seu cliente de e-mail nao suporta HTML. Veja o CSV em anexo.")
    msg.add_alternative(html, subtype="html")
    if csv_bytes:
        msg.add_attachment(csv_bytes, maintype="text", subtype="csv",
                           filename=f"clipping_{date.today()}.csv")
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)
    return recipients
