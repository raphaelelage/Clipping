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


def indices(log=print):
    """[(nome, ultimo, var_dia_pct, var_ano_pct)] — falha de um indice nao derruba os outros."""
    import yfinance as yf
    out = []
    for nome, tk, _ in INDICES:
        try:
            h = yf.Ticker(tk).history(period="ytd")["Close"]
            ult, ontem, ini = float(h.iloc[-1]), float(h.iloc[-2]), float(h.iloc[0])
            out.append((nome, ult, 100 * (ult / ontem - 1), 100 * (ult / ini - 1)))
        except Exception as e:
            log(f"[macro] indice {nome} falhou: {type(e).__name__}")
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
         _t(_bls_cpi_yoy), None, None),
        ("Zona do Euro (depo / HICP)",
         _t(_ecb, "https://data-api.ecb.europa.eu/service/data/FM/"
                  "B.U2.EUR.4F.KR.DFR.LEV?lastNObservations=12&format=csvdata"),
         None, None,
         _t(_ecb, "https://data-api.ecb.europa.eu/service/data/ICP/"
                  "M.U2.N.000000.4.ANR?lastNObservations=12&format=csvdata"),
         None, None),
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

    linhas = "".join(
        f'<tr><td style="{tdl}">{nome}</td>'
        f'<td style="{td}">{_n(ult, 0 if ult > 100 else 2)}</td>'
        f'<td style="{td}{_cor(dia)}">{"+" if dia >= 0 else ""}{_n(dia, 1, True)}</td>'
        f'<td style="{td}{_cor(ano)}">{"+" if ano >= 0 else ""}{_n(ano, 1, True)}</td></tr>'
        for nome, ult, dia, ano in dados)
    return f"""
    <p style="font-family:Arial;font-size:13px;font-weight:bold;color:#111;margin:14px 0 4px 0;">
      Índices &amp; câmbio</p>
    <table style="border-collapse:collapse;">
      <tr><th style="{thl}">Índice</th><th style="{th}">Último</th>
          <th style="{th}">Dia</th><th style="{th}">Ano</th></tr>{linhas}
    </table>"""


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
      Brasil: Selic/IPCA (BCB) e projeções = mediana do Focus. EUA: Fed Funds implícita nos
      futuros (CME) — projeção de mercado, não consenso de economistas; CPI (BLS).
      Euro: taxa de depósito e HICP (BCE). "–" = sem fonte pública confiável.</p>"""


def coletar(log=print):
    """Bloco completo: {'indices': [...], 'macro': [...], 'quando': 'YYYY-MM-DD'}."""
    idx = indices(log=log)
    ji = juros_inflacao(log=log)
    preenchidos = sum(1 for l in ji for v in l[1:] if v is not None)
    log(f"[macro] {len(idx)} indices | {len(ji)} regioes ({preenchidos} celulas com dado)")
    return {"indices": idx, "macro": ji, "quando": date.today().isoformat()}
