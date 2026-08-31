"""Indices e macro (juros/inflacao, atual + projecoes) para o e-mail do clipping.

FONTES (todas testadas, sem chave de API):
  - Indices e cambio ... Yahoo (history ytd: ultimo, dia, ano)
  - Selic meta ......... BCB/SGS serie 432          | IPCA 12m ... BCB/SGS serie 13522
  - Selic 26E/27E ...... Focus/BCB (mediana anual)  | IPCA 26E/27E ... Focus/BCB
  - Fed Funds atual .... futuro ZQ=F (100 - preco)  | 26E/27E ... futuros ZQZ26/ZQZ27
  - CPI EUA 12m ........ BLS v1 (CUUR0000SA0, YoY calculado)
  - ECB depo rate ...... ECB data API (FM/DFR)      | HICP 12m ... ECB data API (ICP ANR)
  - CPI/HICP 26E/27E e depo 26E/27E: SEM fonte publica confiavel — mostram "–"
    (regra da casa: nunca inventar numero; o snapshot Bloomberg pode preencher no futuro)

O resultado e cacheado por dia dentro do valuation_cache.json (chave "_macro") pelo
chamador — este modulo so coleta e formata.
"""
import csv
import io
import sys
from datetime import date

import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

INDICES = [
    ("Ibovespa", "^BVSP", "BR"),
    ("S&P 500", "^GSPC", "US"),
    ("Nasdaq", "^IXIC", "US"),
    ("Dow Jones", "^DJI", "US"),
    ("Euro Stoxx 50", "^STOXX50E", "EU"),
    ("DAX", "^GDAXI", "EU"),
    ("FTSE 100", "^FTSE", "EU"),
    ("USD/BRL", "BRL=X", "FX"),
    ("EUR/BRL", "EURBRL=X", "FX"),
]


def retornos_da_serie(h):
    """(ultimo, 1d, 5d, 1m, ytd, yoy) a partir de uma serie de fechamentos diarios.
    Janelas em PREGOES (5d=5 pregoes, 1m=21, yoy=252 — padrao de mercado); YTD contra o
    ultimo fechamento do ano anterior. Janela maior que o historico -> None."""
    h = h.dropna()
    if len(h) < 2:
        return None
    ult = float(h.iloc[-1])

    def _ret(n):
        return 100 * (ult / float(h.iloc[-(n + 1)]) - 1) if len(h) > n else None
    ano = str(h.index[-1].year)
    antes = h[h.index < ano]
    ytd = 100 * (ult / float(antes.iloc[-1]) - 1) if len(antes) else None
    return (ult, _ret(1), _ret(5), _ret(21), ytd, _ret(252))


def baixar_fechamentos(tickers, log=print):
    """UM request para todos os tickers (yf.download em lote); {ticker: serie}."""
    import yfinance as yf
    px = yf.download(tickers, period="440d", progress=False, auto_adjust=True)["Close"]
    if hasattr(px, "columns"):
        return {tk: px[tk] for tk in px.columns}
    return {tickers[0]: px}


def indices(log=print):
    """[(nome, ultimo, r1d, r5d, r1m, ytd, yoy)] — falha de um nao derruba os outros.
    Quem faltar no download em LOTE ganha uma segunda chance INDIVIDUAL: no Actions o
    lote ja veio sem o USD/BRL uma vez, e cambio sumir da tabela e inaceitavel."""
    import yfinance as yf
    out = []
    try:
        series = baixar_fechamentos([tk for _, tk, _ in INDICES], log=log)
    except Exception as e:
        log(f"[macro] download de indices falhou: {type(e).__name__}")
        series = {}
    for nome, tk, _ in INDICES:
        r = None
        try:
            if tk in series:
                r = retornos_da_serie(series[tk])
        except Exception:
            pass
        if not r:
            try:
                r = retornos_da_serie(yf.Ticker(tk).history(period="440d")["Close"])
                log(f"[macro] {nome}: recuperado no retry individual")
            except Exception as e:
                log(f"[macro] indice {nome} falhou (lote e individual): {type(e).__name__}")
        if r:
            out.append((nome,) + r)
    return out


# ------------------------------------------------------------------ juros/inflacao
def _bcb(serie):
    r = requests.get(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/"
                     f"dados/ultimos/1?formato=json", headers=H, timeout=25)
    return float(r.json()[0]["valor"].replace(",", "."))


def _focus(indicador, ano):
    base = ("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
            "ExpectativasMercadoAnuais")
    u = (f"{base}?$filter=Indicador eq '{indicador}' and DataReferencia eq '{ano}'"
         f"&$orderby=Data desc&$top=1&$format=json")
    v = requests.get(u, headers=H, timeout=25).json()["value"]
    return float(v[0]["Mediana"]) if v else None


def _bls_cpi_yoy():
    r = requests.get("https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0",
                     headers=H, timeout=30)
    serie = r.json()["Results"]["series"][0]["data"]
    atual = serie[0]
    ano_atras = [x for x in serie if x["period"] == atual["period"]
                 and int(x["year"]) == int(atual["year"]) - 1]
    if not ano_atras:
        return None
    return 100 * (float(atual["value"]) / float(ano_atras[0]["value"]) - 1)


def _ecb(url):
    """OBS_VALUE mais recente por TIME_PERIOD (o lastNObservations do ECB as vezes
    devolve vintage antigo — pedimos 12 e escolhemos o periodo maximo)."""
    r = requests.get(url, headers=H, timeout=30)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    rows = [x for x in rows if x.get("OBS_VALUE")]
    if not rows:
        return None
    melhor = max(rows, key=lambda x: x.get("TIME_PERIOD", ""))
    return float(melhor["OBS_VALUE"])


def _oecd(area, medida, ano):
    """Projecao anual do OECD Economic Outlook (alias DF_EO = edicao corrente).
    CPI_YTYPCT/CPIH_YTYPCT = inflacao media anual; IRS = juro de curto prazo (3m)."""
    u = (f"https://sdmx.oecd.org/public/rest/data/OECD.ECO.MAD,DSD_EO@DF_EO,/"
         f"{area}.{medida}.A?startPeriod={ano}&endPeriod={ano}&format=csvfile")
    r = requests.get(u, headers={**H, "Accept": "application/vnd.sdmx.data+csv"},
                     timeout=45)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    for x in rows:
        if x.get("TIME_PERIOD") == str(ano) and x.get("OBS_VALUE"):
            return float(x["OBS_VALUE"])
    return None


def _fed_funds_futuro(tk):
    import yfinance as yf
    p = yf.Ticker(tk).fast_info.last_price
    v = 100 - float(p)
    return v if 0 < v < 12 else None


def juros_inflacao(log=print):
    """Linhas: (regiao, juro_atual, juro_26e, juro_27e, infl_12m, infl_26e, infl_27e).
    Projecoes: BR = Focus (mediana); EUA juro = futuros de fed funds dez/26-dez/27
    ("mercado", nao consenso de economistas); sem fonte -> None ("–" na tabela)."""
    def _t(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            log(f"[macro] {fn.__name__}{a} falhou: {type(e).__name__}")
            return None
    ano0 = date.today().year
    linhas = [
        ("Brasil (Selic / IPCA)",
         _t(_bcb, 432), _t(_focus, "Selic", str(ano0)), _t(_focus, "Selic", str(ano0 + 1)),
         _t(_bcb, 13522), _t(_focus, "IPCA", str(ano0)), _t(_focus, "IPCA", str(ano0 + 1))),
        ("EUA (Fed Funds / CPI)",
         _t(_fed_funds_futuro, "ZQ=F"),
         _t(_fed_funds_futuro, f"ZQZ{str(ano0)[2:]}.CBT"),
         _t(_fed_funds_futuro, f"ZQZ{str(ano0 + 1)[2:]}.CBT"),
         _t(_bls_cpi_yoy),
         _t(_oecd, "USA", "CPI_YTYPCT", ano0), _t(_oecd, "USA", "CPI_YTYPCT", ano0 + 1)),
        ("Zona do Euro (depo / HICP)",
         _t(_ecb, "https://data-api.ecb.europa.eu/service/data/FM/"
                  "B.U2.EUR.4F.KR.DFR.LEV?lastNObservations=12&format=csvdata"),
         _t(_oecd, "EA17", "IRS", ano0), _t(_oecd, "EA17", "IRS", ano0 + 1),
         _t(_ecb, "https://data-api.ecb.europa.eu/service/data/ICP/"
                  "M.U2.N.000000.4.ANR?lastNObservations=12&format=csvdata"),
         _t(_oecd, "EA17", "CPIH_YTYPCT", ano0),
         _t(_oecd, "EA17", "CPIH_YTYPCT", ano0 + 1)),
    ]
    return linhas


# ------------------------------------------------------------------ HTML
def _n(v, casas=1, pct=False):
    if v is None:
        return "–"
    s = f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + ("%" if pct else "")


def html_indices(dados, red="#CC092F"):
    if not dados:
        return ""
    th = (f"padding:3px 8px;font-family:Arial;font-size:11px;color:#fff;background:{red};"
          "text-align:right;white-space:nowrap;")
    thl = th.replace("text-align:right", "text-align:left")
    td = ("padding:3px 8px;font-family:Arial;font-size:11px;color:#222;text-align:right;"
          "border-bottom:1px solid #eee;white-space:nowrap;")
    tdl = td.replace("text-align:right", "text-align:left") + "font-weight:bold;"

    def _cor(v):
        return "color:#0a7d33;" if v >= 0 else "color:#b00020;"

    def _cel(v):
        if v is None:
            return f'<td style="{td}">–</td>'
        return (f'<td style="{td}{_cor(v)}">{"+" if v >= 0 else ""}'
                f'{_n(v, 1, True)}</td>')

    linhas = "".join(
        f'<tr><td style="{tdl}">{l[0]}</td>'
        f'<td style="{td}">{_n(l[1], 0 if l[1] > 100 else 2)}</td>'
        + "".join(_cel(v) for v in l[2:7]) + "</tr>"
        for l in dados)
    return f"""
    <p style="font-family:Arial;font-size:13px;font-weight:bold;color:#111;margin:14px 0 4px 0;">
      Índices &amp; câmbio</p>
    <div style="overflow-x:auto;">
    <table style="border-collapse:collapse;">
      <tr><th style="{thl}">Índice</th><th style="{th}">Último</th>
          <th style="{th}">1d</th><th style="{th}">5d</th><th style="{th}">1m</th>
          <th style="{th}">YTD</th><th style="{th}">YoY</th></tr>{linhas}
    </table></div>"""


def html_macro(linhas, red="#CC092F"):
    if not linhas:
        return ""
    ano0 = date.today().year
    a26, a27 = str(ano0)[2:], str(ano0 + 1)[2:]
    th = (f"padding:3px 8px;font-family:Arial;font-size:11px;color:#fff;background:{red};"
          "text-align:right;white-space:nowrap;")
    thl = th.replace("text-align:right", "text-align:left")
    td = ("padding:3px 8px;font-family:Arial;font-size:11px;color:#222;text-align:right;"
          "border-bottom:1px solid #eee;white-space:nowrap;")
    tdl = td.replace("text-align:right", "text-align:left") + "font-weight:bold;"
    corpo = "".join(
        f'<tr><td style="{tdl}">{reg}</td>'
        + "".join(f'<td style="{td}">{_n(v, 2, True)}</td>'
                  for v in (j0, j1, j2, i0, i1, i2))
        + "</tr>"
        for reg, j0, j1, j2, i0, i1, i2 in linhas)
    return f"""
    <p style="font-family:Arial;font-size:13px;font-weight:bold;color:#111;margin:14px 0 4px 0;">
      Juros &amp; inflação</p>
    <div style="overflow-x:auto;">
    <table style="border-collapse:collapse;">
      <tr><th style="{thl}">Região</th><th style="{th}">Juro</th>
          <th style="{th}">Juro {a26}E</th><th style="{th}">Juro {a27}E</th>
          <th style="{th}">Inflação 12m</th><th style="{th}">Infl. {a26}E</th>
          <th style="{th}">Infl. {a27}E</th></tr>{corpo}
    </table></div>
    <p style="font-family:Arial;font-size:10px;color:#888;margin:4px 0 16px 0;">
      Brasil: Selic/IPCA (BCB); projeções = mediana do Focus. EUA: Fed Funds implícita nos
      futuros (CME) — projeção de mercado; CPI 12m (BLS); CPI 26E/27E = OCDE (média anual).
      Euro: depósito e HICP (BCE); 26E/27E = OCDE (juro de curto prazo 3m e HICP média
      anual). "–" = sem fonte pública confiável.</p>"""


def coletar(log=print):
    """Bloco completo: {'indices': [...], 'macro': [...], 'quando': 'YYYY-MM-DD'}."""
    idx = indices(log=log)
    ji = juros_inflacao(log=log)
    preenchidos = sum(1 for l in ji for v in l[1:] if v is not None)
    log(f"[macro] {len(idx)} indices | {len(ji)} regioes ({preenchidos} celulas com dado)")
    return {"v": 3, "indices": idx, "macro": ji, "quando": date.today().isoformat()}
