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
import os
import json
import time
import re
import zipfile
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser
from bs4 import BeautifulSoup

ARQUIVO_RX = re.compile(r"\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?|pptx?)(/view)?$", re.I)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TIMEOUT = 12          # curto de proposito: se a fonte esta fora, seguir em frente

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

# Feeds AMPLOS dos grandes veiculos: entram com filtro de keyword (filtrar=True), senao viram
# ruido. Vantagem sobre o Google News: sem risco de bloqueio e com data confiavel.
GRANDES_ECONOMIA = [
    ("G1", "https://g1.globo.com/rss/g1/economia/", True),
    ("O Globo", "https://oglobo.globo.com/rss/oglobo/economia", True),
    ("Folha de S.Paulo", "https://feeds.folha.uol.com.br/mercado/rss091.xml", True),
    ("Agência Brasil", "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml", True),
    ("Estadão", "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/?outputType=xml", True),
    ("UOL Economia", "https://rss.uol.com.br/feed/economia.xml", True),
]

RSS_FEEDS = {
    "saude": [
        ("G1", "https://g1.globo.com/rss/g1/saude/", True),
        ("O Globo", "https://oglobo.globo.com/rss/oglobo/saude", True),
        ("Folha de S.Paulo", "https://feeds.folha.uol.com.br/equilibrioesaude/rss091.xml", True),
        ("Agência Brasil", "https://agenciabrasil.ebc.com.br/rss/saude/feed.xml", True),
        ("Estadão", "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/saude/?outputType=xml", True),
        *GRANDES_ECONOMIA,
        ("Medicina S/A", "https://medicinasa.com.br/feed/", False),
        ("Setor Saúde", "https://www.setorsaude.com.br/feed/", False),
        # herdeiras do papel de curadoria (achadas na busca por concorrentes do Scoop.it):
        ("Futuro da Saúde", "https://futurodasaude.com.br/feed/", False),
        ("Valor & Saúde", "https://valoresaude.substack.com/feed", False),
        ("Fiocruz", "https://agencia.fiocruz.br/rss-afn.xml", True),
        ("JOTA", "https://www.jota.info/feed", True),
        ("CADE", "https://www.gov.br/cade/rss.xml", True, "/noticias/"),
        ("Consumidor Moderno", "https://www.consumidormoderno.com.br/feed/", True),
    ],
    "educacao": [
        ("G1", "https://g1.globo.com/rss/g1/educacao/", True),
        ("Folha de S.Paulo", "https://feeds.folha.uol.com.br/educacao/rss091.xml", True),
        ("Agência Brasil", "https://agenciabrasil.ebc.com.br/rss/educacao/feed.xml", True),
        ("Jornal da USP", "https://jornal.usp.br/feed/", True),
        *GRANDES_ECONOMIA,
        ("INEP", "https://www.gov.br/inep/rss.xml", False, "/noticias/"),
        ("JOTA", "https://www.jota.info/feed", True),
        ("CADE", "https://www.gov.br/cade/rss.xml", True, "/noticias/"),
    ],
}

# Diario Oficial da Uniao — FRASE EXATA (busca livre e OR de palavras => ruido demais).
# Termos TEMATICOS (regulacao, vagas, registros...) + algumas marcas. Nao depende de nome de grupo:
# os atos citam a instituicao/tema, nao a holding.
DOU_TERMOS = {
    "saude": [
        # regulacao de planos / ANS
        "Agência Nacional de Saúde Suplementar", "saúde suplementar", "operadora de plano de saúde",
        "rol de procedimentos", "ressarcimento ao SUS", "reajuste de planos",
        # medicamentos / Anvisa
        "registro de medicamento", "cancelamento de registro", "medicamento genérico",
        "certificado de boas práticas", "suspensão de venda", "interdição cautelar",
        "preço de medicamentos", "CMED",
        # SUS / incorporacao
        "incorporação de tecnologia", "Conitec", "Farmácia Popular", "tabela SUS",
        # empresas
        "Hapvida", "Rede D'Or", "Dasa", "Oncoclínicas", "Qualicorp", "Hypera", "Blau",
    ],
    "educacao": [
        # vagas e cursos (inclui o caso 'vagas de medicina')
        "autorização de curso de Medicina", "curso de Medicina", "aumento de vagas",
        "vagas autorizadas", "autorização de funcionamento",
        # regulacao / supervisao de instituicoes
        "credenciamento", "recredenciamento", "descredenciamento",
        "reconhecimento de curso", "renovação de reconhecimento", "medida cautelar",
        "educação a distância", "polo de educação a distância",
        # normas e programas
        "Conselho Nacional de Educação", "Câmara de Educação Superior",
        "diretrizes curriculares nacionais", "Fies", "Prouni", "Enem", "Enade",
        # marcas (os atos citam a instituicao, nao a holding)
        "Estácio", "Anhanguera", "Anhembi Morumbi", "Uniasselvi", "Unicesumar", "Afya",
    ],
}

# Só entram atos destes orgaos — mata o ruido cross-setor (o mesmo termo aparece em
# ANTAQ, Agricultura, Fazenda etc.). Comparacao sem acento, em minusculas.
DOU_ORGAOS = {
    "saude": ["ministerio da saude", "vigilancia sanitaria", "saude suplementar",
              "defesa economica"],
    "educacao": ["ministerio da educacao", "estudos e pesquisas educacionais",
                 "aperfeicoamento de pessoal", "desenvolvimento da educacao",
                 "defesa economica"],
}

# CVM — fato relevante / comunicado ao mercado das empresas cobertas
CVM_EMPRESAS = {
    "saude": ["HAPVIDA", "REDE D'OR", "REDE DOR", "DASA", "DIAGNOSTICOS DA AMERICA",
              "ONCOCLINICAS", "QUALICORP", "HYPERA", "BLAU", "VIVEO", "MATER DEI",
              "RAIA DROGASIL", "PAGUE MENOS", "DIMED", "ODONTOPREV", "FLEURY"],
    "educacao": ["COGNA", "YDUQS", "SER EDUCACIONAL", "ANIMA", "VASTA", "AFYA", "VITRU",
                 "CRUZEIRO DO SUL", "ARCO"],
}
# vertical combinada = uniao das duas listas (sem repetir), montada automaticamente
def _uniao(d, chave):
    vis, out = set(), []
    for x in d.get("saude", []) + d.get("educacao", []):
        k = chave(x)
        if k not in vis:
            vis.add(k); out.append(x)
    return out

WP_SITES["saude_educacao"] = _uniao(WP_SITES, lambda x: x[1])
RSS_FEEDS["saude_educacao"] = _uniao(RSS_FEEDS, lambda x: x[1])
DOU_TERMOS["saude_educacao"] = _uniao(DOU_TERMOS, lambda x: x.lower())
DOU_ORGAOS["saude_educacao"] = _uniao(DOU_ORGAOS, lambda x: x)
CVM_EMPRESAS["saude_educacao"] = _uniao(CVM_EMPRESAS, lambda x: x)

CVM_CATEGORIAS = ("Fato Relevante", "Comunicado ao Mercado")
CVM_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
RAD_URL = "https://www.rad.cvm.gov.br/ENET/frmConsultaExternaCVM.aspx"
RAD_TIPOS = ("Fato Relevante", "Comunicado ao Mercado", "Aviso aos Acionistas")

# Empresas que sao ADR na NASDAQ e NAO aparecem na CVM (buraco de cobertura).
# A SEC exige User-Agent com contato (politica deles).
SEC_EMPRESAS = {"saude": {}, "educacao": {"Afya": "0001771007"}}
SEC_EMPRESAS["saude_educacao"] = {**SEC_EMPRESAS["saude"], **SEC_EMPRESAS["educacao"]}
SEC_FORMS = ("6-K", "20-F", "8-K")

# Paginas de curadoria no Scoop.it. Cada card ja traz TUDO no proprio HTML: o link
# ORIGINAL da noticia (nao o do scoop.it), a data de curadoria e a data de publicacao
# da noticia no site original — entao a coleta nao visita noticia nenhuma.
SCOOPIT = {
    "saude": [],
    "educacao": [("Educação 3.0 (Scoop.it)",
                  "https://www.scoop.it/topic/educacao-3-0-uma-jornada")],
}
SCOOPIT["saude_educacao"] = SCOOPIT["saude"] + SCOOPIT["educacao"]
# A SEC EXIGE User-Agent com e-mail de contato: sem e-mail ela devolve HTTP 403 (medido).
# Como o repositorio e publico, o e-mail NUNCA fica no codigo — vem do ambiente:
# variavel de repo SEC_CONTATO (preferida) ou o secret EMAIL_REMETENTE ja existente.
# Sem nenhum dos dois a SEC e simplesmente pulada, sem quebrar a coleta.
def _sec_headers():
    contato = (os.environ.get("SEC_CONTATO") or os.environ.get("EMAIL_REMETENTE") or "").strip()
    if "@" not in contato:
        return None
    return {"User-Agent": "Clipping Bot " + contato, "Accept-Encoding": "gzip, deflate"}


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


# ATENCAO: o WAF do in.gov.br DERRUBA a conexao se o User-Agent nao parecer navegador
# (testado: UA identificavel tipo "clipping-bot/1.0" => ConnectionError em 100% dos termos).
# Por isso mantemos o UA de navegador. O fallback para HTTP cobre falhas de TLS
# vistas a partir dos runners do GitHub.
DOU_HEADERS = {**HEADERS,
               "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}

def _dou_get(url):
    for u in (url, url.replace("https://", "http://", 1)):
        try:
            r = requests.get(u, headers=DOU_HEADERS, timeout=12)
            if r.status_code == 200:
                return r
        except Exception:
            continue
    return None

def _dou(termo, from_date, ctx, orgaos=()):
    """Busca por FRASE EXATA no DOU. Devolve atos (portarias, decisoes, extratos)."""
    # NAO usar exactDate=dia: devolve 0 mesmo havendo atos publicados. Minimo = semana,
    # e a filtragem fina por data e feita abaixo (d0 < from_date).
    dias = (date.today() - from_date).days
    janela = "semana" if dias <= 7 else "mes"
    url = ("https://www.in.gov.br/consulta/-/buscar/dou?q=%22"
           + requests.utils.quote(termo) + f"%22&s=do1,do1e&exactDate={janela}&sortType=0")  # DO1 + Edicao Extra
    rows = []
    try:
        r = _dou_get(url)
        if r is None:
            ctx.setdefault("dou_falhas", []).append(termo)
            return rows
        m = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            ctx.setdefault("dou_falhas", []).append(termo)
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
            hier = it.get("hierarchyStr") or ""
            if orgaos and not any(o in ctx["norm"](hier) for o in orgaos):
                continue          # ato de outro setor (ANTAQ, Agricultura...) — descarta
            orgao = hier.split("/")[-1].strip()
            # o titulo do DOU sozinho e inutil ("DECISAO de 7 de agosto"); o trecho do ato
            # e o que informa (ex.: "Processo ANS ... HAPVIDA ... Valor da Multa")
            trecho = re.sub(r"<[^>]+>", "", it.get("content") or "")
            trecho = re.sub(r"\s+", " ", trecho).strip()[:150]
            titulo_full = titulo + (f" — {orgao}" if orgao else "") + (f": {trecho}" if trecho else "")
            rows.append((titulo_full[:230], "DOU",
                         d0.strftime("%a, %d %b %Y") if d0 else "", "",
                         f"DOU: {termo}", link, "https://www.in.gov.br"))
    except Exception:
        ctx.setdefault("dou_falhas", []).append(termo)
    return rows


_RX_EMPRESA_CACHE = {}


def _rx_empresas(empresas):
    """Casa o nome da empresa por PALAVRA INTEIRA. Substring simples deixava
    "ARCO" (Arco Educacao) casar com MARCOPOLO e ARCOS DORADOS (McDonald's)."""
    chave = tuple(empresas)
    rx = _RX_EMPRESA_CACHE.get(chave)
    if rx is None:
        alt = "|".join(re.escape(e.upper()) for e in empresas)
        rx = re.compile(r"\b(?:" + alt + r")\b")
        _RX_EMPRESA_CACHE[chave] = rx
    return rx


def _cvm_rad(empresas, from_date, ctx):
    """Fato relevante / comunicado direto do sistema da CVM (RAD) — no MESMO DIA.
    O zip anual do IPE (_cvm) so traz o dado consolidado no dia seguinte; este e o endpoint
    que o proprio site da CVM usa. Nao e documentado — por isso e primaria com o zip de reserva.
    Devolve None se falhar (sinal para o chamador usar o zip)."""
    dias = (date.today() - from_date).days
    # "1d" pede a SEMANA e filtra por data localmente: pedir "hoje" perderia o fato relevante
    # publicado ontem a noite, que a rodada das 06h45 precisa pegar.
    periodo = "0" if dias < 1 else ("1" if dias <= 7 else "2")   # hoje / semana / mes
    try:
        ses = requests.Session()
        ses.headers.update(HEADERS)
        ses.get(RAD_URL, timeout=TIMEOUT)                          # cookies de sessao
        payload = {"dataDe": "", "dataAte": "", "empresa": "", "setorAtividade": "-1",
                   "categoriaEmissor": "-1", "situacaoEmissor": "-1", "tipoParticipante": "-1",
                   "dataReferencia": "", "categoria": "", "periodo": periodo, "horaIni": "",
                   "horaFim": "", "palavraChave": "", "ultimaDtRef": "false",
                   "tipoEmpresa": "0", "token": "", "versaoCaptcha": ""}
        r = ses.post(RAD_URL + "/ListarDocumentos", json=payload, timeout=25,
                     headers={"Content-Type": "application/json; charset=UTF-8",
                              "Referer": RAD_URL})
        d = r.json().get("d") or {}
        if d.get("temErro"):
            return None
        dados = d.get("dados") or ""
    except Exception:
        return None
    rows = []
    rx = _rx_empresas(empresas)
    for bruto in dados.split("&*"):
        c = bruto.split("$&")
        if len(c) < 11:
            continue
        nome, tipo, status = c[1], c[2], c[7]
        if status.strip().lower() != "ativo":          # descarta documento cancelado
            continue
        if tipo not in RAD_TIPOS or not rx.search(nome.upper()):
            continue
        m = re.search(r"(\d{2}/\d{2}/\d{4})", c[6])
        try:
            d0 = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
        except Exception:
            d0 = None
        if d0 and d0 < from_date:
            continue
        prot = re.search(r"NumeroProtocoloEntrega=(\d+)", c[10])
        link = ("https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx"
                "?NumeroProtocoloEntrega=" + prot.group(1)) if prot else RAD_URL
        assunto = (c[3] or "").strip()
        desc = re.sub(r"<[^>]+>", "", c[4] or "").strip(" -")
        titulo = nome.title() + " \u2014 " + tipo
        if desc or assunto:
            titulo += ": " + (desc or assunto)
        rows.append((titulo[:200], "CVM", d0.strftime("%a, %d %b %Y") if d0 else "", "",
                     "CVM: " + tipo, link, "https://www.rad.cvm.gov.br"))
    return rows


def _sec(empresas, from_date, ctx):
    """Filings da SEC das empresas que sao ADR e nao aparecem na CVM (ex.: Afya na NASDAQ)."""
    headers = _sec_headers()
    if headers is None:
        log = (ctx or {}).get("log")
        if log:
            log("[fontes_extra] SEC pulada: defina SEC_CONTATO (e-mail) — a SEC exige contato no User-Agent")
        return []
    rows = []
    for nome, cik in (empresas or {}).items():
        try:
            r = requests.get("https://data.sec.gov/submissions/CIK" + cik + ".json",
                             headers=headers, timeout=20)
            if r.status_code != 200:
                continue
            rec = (r.json().get("filings") or {}).get("recent") or {}
            for f, dt, doc, acc in zip(rec.get("form", []), rec.get("filingDate", []),
                                       rec.get("primaryDocument", []),
                                       rec.get("accessionNumber", [])):
                if f not in SEC_FORMS:
                    continue
                try:
                    d0 = datetime.strptime(dt, "%Y-%m-%d").date()
                except Exception:
                    continue
                if d0 < from_date:
                    continue
                link = ("https://www.sec.gov/Archives/edgar/data/" + str(int(cik)) + "/"
                        + acc.replace("-", "") + "/" + doc)
                rows.append((nome + " \u2014 SEC " + f + " (NASDAQ)", "SEC",
                             d0.strftime("%a, %d %b %Y"), "", "SEC: " + f, link,
                             "https://www.sec.gov"))
        except Exception:
            pass
    return rows


def _cvm(empresas, from_date, ctx):
    """Fatos relevantes / comunicados ao mercado (dados abertos da CVM)."""
    rows = []
    try:
        r = requests.get(CVM_URL.format(ano=from_date.year), headers=HEADERS, timeout=40)
        if r.status_code != 200:
            return rows
        z = zipfile.ZipFile(io.BytesIO(r.content))
        linhas = z.read(z.namelist()[0]).decode("latin-1").splitlines()
        cab = linhas[0].split(";")
        idx = {c: i for i, c in enumerate(cab)}
        rx = _rx_empresas(empresas)
        for ln in linhas[1:]:
            c = ln.split(";")
            if len(c) < len(cab):
                continue
            nome = c[idx["Nome_Companhia"]].upper()
            if not rx.search(nome):
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
def _scoopit(nome, base_url, cutoff, ctx, max_pag=8):
    """Scoop.it: compilado de noticias curado a mao, sem RSS e sem filtro de data.

    REGRA DA JANELA: o criterio e a data de PUBLICACAO da noticia no site original,
    que o proprio card informa (title="Publication date") — nenhuma noticia precisa ser
    visitada. A varredura para quando a CURADORIA sai da janela: como ninguem cura uma
    noticia antes de ela ser publicada, publicacao <= curadoria sempre — entao curadoria
    fora da janela implica publicacao fora tambem, e as paginas seguintes so teriam
    coisa mais velha. Card sem data de publicacao (raro) usa a de curadoria.

    O link devolvido e o do <a> do titulo, que aponta DIRETO para o site original."""
    import dateparser
    from urllib.parse import urlparse

    agora = datetime.now(ctx["tz"])
    cfg_data = {"languages": ["en"],
                "settings": {"PREFER_DATES_FROM": "past",
                             "RELATIVE_BASE": agora.replace(tzinfo=None)}}

    def _data(txt):
        try:
            d = dateparser.parse((txt or "").strip(), **cfg_data)
            return d.replace(tzinfo=ctx["tz"]) if d else None
        except Exception:
            return None

    rows, vistos = [], set()
    for pag in range(1, max_pag + 1):
        try:
            r = requests.get(f"{base_url}?nosug=1&page={pag}", headers=HEADERS, timeout=25)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "lxml")
        except Exception:
            break

        # maquina de estados na ordem do documento: cada div.from_curationDate abre um
        # card; titulo/link, data de publicacao e trecho vao caindo no card aberto
        cards, atual = [], None
        for el in soup.find_all(["div", "h2", "a", "blockquote"]):
            classes = el.get("class") or []
            if el.name == "div" and "from_curationDate" in classes:
                if atual and atual.get("link"):
                    cards.append(atual)
                atual = {"cur": _data(el.get_text(" ", strip=True))}
            elif atual is not None and el.name == "h2" and "postTitleView" in classes:
                a = el.find("a", href=True)
                if a and a["href"].startswith("http") and "scoop.it" not in a["href"]:
                    atual["link"] = a["href"]
                    atual["titulo"] = a.get_text(" ", strip=True)
            elif (atual is not None and el.name == "a"
                  and str(el.get("title", "")).startswith("Publication date")):
                atual["pub"] = _data(el.get_text(" ", strip=True))
            elif atual is not None and el.name == "blockquote" and "trecho" not in atual:
                atual["trecho"] = el.get_text(" ", strip=True)[:400]
        if atual and atual.get("link"):
            cards.append(atual)
        if not cards:
            break

        alguma_dentro = False
        for c in cards:
            cur = c.get("cur")
            if cur and cur >= cutoff:
                alguma_dentro = True
            ref = c.get("pub") or cur          # criterio final: data da noticia original
            if not ref or ref < cutoff or ref > agora + timedelta(hours=12):
                continue
            link = c["link"]
            if link in vistos:
                continue
            vistos.add(link)
            titulo = c.get("titulo", "").strip()
            kw = ctx["match"](titulo + " " + c.get("trecho", ""))
            if not kw:
                continue                        # mesmos filtros de keyword do clipping
            dominio = urlparse(link).netloc.replace("www.", "")
            d, h = _fmt(ref, ctx["tz"])
            rows.append((titulo, dominio, d, h, kw, link, f"https://{dominio}"))
        if not alguma_dentro:
            break                               # curadoria da pagina inteira fora da janela
    return rows


def coletar(vertical, cutoff, from_date, match_fn, to_dt_fn, tz, log=print, norm_fn=None):
    """Coleta de todas as fontes extras da vertical, em paralelo.
    Devolve linhas no formato COLS do clipping_core."""
    ctx = {"match": match_fn, "to_dt": to_dt_fn, "tz": tz, "norm": norm_fn}
    norm_fn = norm_fn or (lambda x: str(x).lower())
    ctx["norm"] = norm_fn
    v = vertical if vertical in WP_SITES else "saude"
    # secao criada no app: herda as fontes estruturais (WP/RSS/DOU/CVM/SEC/scoop) da(s)
    # base(s) definidas no verticais.json — uniao sem repetir
    if vertical not in WP_SITES:
        try:
            import clipping_core as _cc
            bases = _cc.BASES_RAIZ.get(vertical)
        except Exception:
            bases = None
        if bases:
            v = "saude_educacao" if set(bases) == {"saude", "educacao"} else bases[0]
    tarefas = []
    for nome, base, filtrar in WP_SITES.get(v, []):
        tarefas.append((f"wp:{nome}", lambda n=nome, b=base, f=filtrar: _wp(n, b, f, from_date, ctx)))
    for item in RSS_FEEDS.get(v, []):
        nome, url, filtrar = item[0], item[1], item[2]
        exige = item[3] if len(item) > 3 else None
        tarefas.append((f"rss:{nome}",
                        lambda n=nome, u=url, f=filtrar, e=exige: _rss(n, u, f, cutoff, ctx, e)))
    for nome, base in SCOOPIT.get(v, []):
        tarefas.append((f"scoopit:{nome}",
                        lambda n=nome, b=base: _scoopit(n, b, cutoff, ctx)))
    orgaos = DOU_ORGAOS.get(v, ())
    for termo in DOU_TERMOS.get(v, []):
        tarefas.append((f"dou:{termo}", lambda t=termo: _dou(t, from_date, ctx, orgaos)))
    if CVM_EMPRESAS.get(v):
        def _cvm_com_reserva(_v=v):
            got = _cvm_rad(CVM_EMPRESAS[_v], from_date, ctx)      # tempo real
            if got is None:
                if log:
                    log("[fontes_extra] CVM: RAD indisponivel — caindo no zip do IPE")
                return _cvm(CVM_EMPRESAS[_v], from_date, ctx)     # reserva
            return got
        tarefas.append(("cvm", _cvm_com_reserva))
    if SEC_EMPRESAS.get(v):
        tarefas.append(("sec", lambda _v=v: _sec(SEC_EMPRESAS[_v], from_date, ctx)))

    rows, por_fonte = [], {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(fn): nome for nome, fn in tarefas}
        for f in as_completed(futs):
            try:
                got = f.result() or []
            except Exception:
                got = []
            if got:
                por_fonte[futs[f].split(":")[0]] = por_fonte.get(futs[f].split(":")[0], 0) + len(got)
            rows.extend(got)
    # se TODOS os termos do DOU falharam, quase sempre e o in.gov.br fora do ar naquele
    # instante (visto em runners do GitHub). Uma retentativa depois de uma pausa recupera.
    termos_dou = DOU_TERMOS.get(v, [])
    if termos_dou and len(ctx.get("dou_falhas") or []) >= len(termos_dou):
        if log:
            log(f"[fontes_extra/{v}] DOU indisponivel — tentando de novo em 15s")
        time.sleep(15)
        ctx["dou_falhas"] = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for got in ex.map(lambda t: _dou(t, from_date, ctx, DOU_ORGAOS.get(v, ())),
                              termos_dou):
                if got:
                    por_fonte["dou"] = por_fonte.get("dou", 0) + len(got)
                    rows.extend(got)

    if log:
        resumo = " ".join(f"{k}={v2}" for k, v2 in sorted(por_fonte.items())) or "nada"
        falhas = ctx.get("dou_falhas") or []
        extra = f" | DOU indisponivel em {len(falhas)} termo(s)" if falhas else ""
        log(f"[fontes_extra/{v}] {len(tarefas)} fontes -> {len(rows)} itens ({resumo}){extra}")
    return rows
