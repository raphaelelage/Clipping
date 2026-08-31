"""Summary de valuation das empresas cobertas, para o topo do e-mail do clipping.

CADEIA DE FONTES (nesta ordem, por campo):
  1. Bloomberg  — snapshot gravado pelo PC do usuario quando o terminal esta aberto
                  (valuation_bbg.py); usado se tiver menos de 5 dias. E o unico com
                  EBITDA 26E/27E e ROIC de consenso.
  2. Yahoo      — yfinance, gratis e comprovado para a cobertura (19/20 tickers com
                  preco, alvo de consenso, P/E fwd, estimativas de receita/EPS 0y/+1y).
  3. Cache      — ultimo valor bom (valuation_cache.json no Drive); entra quando as
                  fontes vivas falham, marcado com a data.

REGRAS DA CASA:
  - NUNCA estimar numero que a fonte nao deu (sem "EBITDA 27E = receita x margem") —
    celula sem fonte mostra "–".
  - total return = (alvo/preco - 1) + dividend yield 12m (quando o Yahoo informar o
    yield; senao vira upside puro — a legenda do e-mail explica).
  - Estimativas anuais do Yahoo: 0y = ano corrente (2026), +1y = seguinte (2027).
  - Lucro 26E/27E = EPS estimado x acoes em circulacao.
  - ROIC (fallback Yahoo) e TRAILING, calculado como EBIT x (1 - 25%) sobre
    (PL + divida liquida) — aproximacao contabil, marcada como 12m na legenda.

Config editavel pelo app: empresas_valuation_<vertical>.txt (um ticker Yahoo por linha,
formato B3 = XXXX3.SA; ADR = ticker US). Vertical combinada = uniao das duas listas.
"""
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_EMPRESAS = {
    "saude": ["HAPV3.SA", "RDOR3.SA", "DASA3.SA", "ONCO3.SA", "QUAL3.SA", "HYPE3.SA",
              "BLAU3.SA", "VVEO3.SA", "MATD3.SA", "RADL3.SA", "PGMN3.SA", "PNVL3.SA",
              "FLRY3.SA"],
    "educacao": ["COGN3.SA", "YDUQ3.SA", "SEER3.SA", "ANIM3.SA", "VTRU3.SA", "AFYA"],
}

CAMPOS = ["preco", "alvo", "total_return", "mktcap", "adtv", "pe_26e", "ev_ebitda",
          "receita_26e", "receita_27e", "ebitda_26e", "ebitda_27e", "lucro_26e",
          "lucro_27e", "dl_ebitda", "roe", "roic"]


def empresas_da_vertical(vertical):
    """Le a lista editavel; combinada = uniao. Sem arquivo, usa o default embutido."""
    def _ler(v):
        caminho = os.path.join(BASE, f"empresas_valuation_{v}.txt")
        if os.path.exists(caminho):
            linhas = [l.strip().upper() for l in io.open(caminho, encoding="utf-8")
                      if l.strip() and not l.strip().startswith("#")]
            return linhas
        return list(DEFAULT_EMPRESAS.get(v, []))
    # heranca do verticais.json (mesma regra das keywords): lista propria manda;
    # vazia/ausente -> uniao das bases herdadas
    try:
        with io.open(os.path.join(BASE, "verticais.json"), encoding="utf-8") as fh:
            herda = (json.load(fh).get(vertical) or {}).get("herda") or []
    except Exception:
        herda = []
    if vertical in ("saude", "educacao"):
        return _ler(vertical)
    proprio_path = os.path.join(BASE, f"empresas_valuation_{vertical}.txt")
    proprio = _ler(vertical) if os.path.exists(proprio_path) else []
    if proprio:
        return proprio
    bases = herda or ["saude", "educacao"]
    vistos, uniao = set(), []
    for v in bases:
        for t in _ler(v):
            if t not in vistos:
                vistos.add(t)
                uniao.append(t)
    return uniao


# ------------------------------------------------------------------ fonte Yahoo
def _yahoo_um(tk):
    """Todos os campos que o Yahoo entrega para um ticker. Levanta excecao se falhar."""
    import yfinance as yf
    t = yf.Ticker(tk)
    i = t.info
    preco = i.get("currentPrice")
    if not preco:
        raise RuntimeError("sem preco")
    alvo = i.get("targetMeanPrice")
    dy = i.get("dividendYield") or 0            # ja vem em %, ex.: 5.2
    shares = i.get("sharesOutstanding")
    d = {
        "preco": preco,
        "alvo": alvo,
        "total_return": ((alvo / preco - 1) * 100 + (dy if dy and dy < 30 else 0))
                        if alvo else None,
        "mktcap": i.get("marketCap"),
        "adtv": (i.get("averageDailyVolume3Month") or 0) * preco or None,
        "ev_ebitda": i.get("enterpriseToEbitda"),
        "dl_ebitda": ((i.get("totalDebt") or 0) - (i.get("totalCash") or 0))
                     / i["ebitda"] if i.get("ebitda") else None,
        "roe": (i.get("returnOnEquity") or 0) * 100 or None,
        "analistas": i.get("numberOfAnalystOpinions"),
        "moeda": i.get("financialCurrency") or "BRL",
    }
    # estimativas anuais (0y = ano corrente, +1y = seguinte)
    try:
        re_ = t.revenue_estimate
        d["receita_26e"] = float(re_.loc["0y", "avg"]) if "0y" in re_.index else None
        d["receita_27e"] = float(re_.loc["+1y", "avg"]) if "+1y" in re_.index else None
    except Exception:
        pass
    moeda_cotacao = i.get("currency") or ""
    d["moeda_mista"] = bool(moeda_cotacao and d["moeda"] and moeda_cotacao != d["moeda"])
    try:
        ee = t.earnings_estimate
        eps0 = float(ee.loc["0y", "avg"]) if "0y" in ee.index else None
        eps1 = float(ee.loc["+1y", "avg"]) if "+1y" in ee.index else None
        n0 = int(ee.loc["0y", "numberOfAnalysts"]) if "0y" in ee.index else 0
        # P/E 26E so quando a estimativa e CONFIAVEL: mesma moeda da cotacao (ADR tem
        # preco em USD e EPS em BRL — dividir daria 1,5x para a Afya, lixo) e consenso
        # com pelo menos 4 analistas (o "0y" do Yahoo as vezes tem 2 e destoa).
        # Fora disso, usa o forwardPE do proprio Yahoo, que e coerente em moeda.
        eps_confiavel = eps0 and eps0 > 0 and n0 >= 4 and not d["moeda_mista"]
        d["pe_26e"] = (preco / eps0) if eps_confiavel else i.get("forwardPE")
        # lucro em moeda dos DEMONSTRATIVOS (mesma da receita) — coerente entre si
        if eps0 and shares:
            d["lucro_26e"] = eps0 * shares
        if eps1 and shares:
            d["lucro_27e"] = eps1 * shares
    except Exception:
        d.setdefault("pe_26e", i.get("forwardPE"))
    # ROIC trailing (aproximacao: EBIT x 0.75 / capital investido)
    try:
        inc = t.income_stmt
        bal = t.balance_sheet
        ebit = float(inc.loc["EBIT"].iloc[0])
        pl = float(bal.loc["Stockholders Equity"].iloc[0])
        divida = float(bal.loc["Total Debt"].iloc[0]) if "Total Debt" in bal.index else 0
        caixa = (float(bal.loc["Cash And Cash Equivalents"].iloc[0])
                 if "Cash And Cash Equivalents" in bal.index else 0)
        cap = pl + divida - caixa
        if cap > 0:
            d["roic"] = ebit * 0.75 / cap * 100
    except Exception:
        pass
    return d


def _yahoo(tickers, log=print):
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_yahoo_um, tk): tk for tk in tickers}
        for f in as_completed(futs):
            tk = futs[f]
            try:
                out[tk] = f.result()
                out[tk]["_fonte"] = "yahoo"
            except Exception as e:
                log(f"[valuation] yahoo falhou para {tk}: {type(e).__name__}")
    return out


# ------------------------------------------------------------- snapshot Bloomberg
def _bloomberg(caminho_json, max_dias=5):
    """Snapshot opcional gravado pelo PC do usuario (valuation_bbg.py) — nao roda aqui.
    Formato: {"quando": "YYYY-MM-DD", "dados": {ticker: {campo: valor}}}."""
    try:
        with io.open(caminho_json, encoding="utf-8") as fh:
            snap = json.load(fh)
        idade = (date.today() - date.fromisoformat(snap["quando"])).days
        if idade > max_dias:
            return {}, None
        return snap.get("dados", {}), snap["quando"]
    except Exception:
        return {}, None


# ------------------------------------------------------------------ orquestrador
def coletar(vertical, bbg_json="bbg_snapshot.json", cache_json="valuation_cache.json",
            log=print):
    """Monta {ticker: {campo: valor, _fonte, _quando}} pela cadeia BBG > Yahoo > cache.
    Grava o cache atualizado (quem chama decide subir para o Drive)."""
    tickers = empresas_da_vertical(vertical)
    if not tickers:
        return {}

    try:
        with io.open(os.path.join(BASE, cache_json), encoding="utf-8") as fh:
            _c = json.load(fh)
        if all(_c.get(t, {}).get("_quando") == date.today().isoformat() for t in tickers):
            log(f"[valuation] cache de hoje completo — sem consulta externa")
            return {t: _c[t] for t in tickers}
    except Exception:
        pass

    bbg, bbg_quando = _bloomberg(os.path.join(BASE, bbg_json))
    yah = _yahoo(tickers, log=log)
    try:
        with io.open(os.path.join(BASE, cache_json), encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}

    hoje = date.today().isoformat()
    final = {}
    for tk in tickers:
        linha = {}
        vivo = yah.get(tk, {})
        velho = cache.get(tk, {})
        for c in CAMPOS + ["analistas", "moeda", "moeda_mista"]:
            if bbg.get(tk, {}).get(c) is not None:      # BBG ganha (unico com 26E/27E completos)
                linha[c] = bbg[tk][c]
                linha.setdefault("_fontes", {})[c] = f"bbg {bbg_quando}"
            elif vivo.get(c) is not None:
                linha[c] = vivo[c]
                linha.setdefault("_fontes", {})[c] = "yahoo"
            elif velho.get(c) is not None:
                linha[c] = velho[c]
                linha.setdefault("_fontes", {})[c] = velho.get("_fontes", {}).get(c, "cache")
        linha["_quando"] = hoje if vivo else velho.get("_quando", "?")
        final[tk] = linha

    cache.update(final)
    try:
        with io.open(os.path.join(BASE, cache_json), "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
    except Exception:
        pass
    ok = sum(1 for v in final.values() if v.get("preco"))
    log(f"[valuation] {ok}/{len(tickers)} empresas com dados "
        f"(bbg={'sim, ' + bbg_quando if bbg_quando else 'nao'})")
    return final


# ------------------------------------------------------------------ formatacao
def _fm(v, tipo):
    if v is None:
        return "–"
    try:
        if tipo == "preco":
            return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if tipo == "pct":
            return f"{v:+.0f}%"
        if tipo == "bi":
            return f"{v/1e9:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if tipo == "mi":
            return f"{v/1e6:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if tipo == "x":
            return f"{v:.1f}x"
        if tipo == "pct1":
            return f"{v:.0f}%"
    except Exception:
        return "–"
    return str(v)


def tabela_html(dados, red="#CC092F"):
    """Duas tabelas compactas (mercado + estimativas), prontas para o e-mail."""
    if not dados:
        return ""
    th = ("padding:3px 6px;font-family:Arial;font-size:11px;color:#fff;"
          f"background:{red};text-align:right;white-space:nowrap;")
    thl = th.replace("text-align:right", "text-align:left")
    td = ("padding:3px 6px;font-family:Arial;font-size:11px;color:#222;"
          "text-align:right;border-bottom:1px solid #eee;white-space:nowrap;")
    tdl = td.replace("text-align:right", "text-align:left") + "font-weight:bold;"

    def _linha(tk, d, cols):
        nome = tk.replace(".SA", "") + ("*" if d.get("moeda_mista") else "")
        cs = "".join(f'<td style="{td}">{c}</td>' for c in cols)
        return f'<tr><td style="{tdl}">{nome}</td>{cs}</tr>'

    # ordena por market cap
    ordem = sorted(dados.items(), key=lambda kv: -(kv[1].get("mktcap") or 0))

    l1 = "".join(_linha(tk, d, [
        _fm(d.get("preco"), "preco"), _fm(d.get("alvo"), "preco"),
        _fm(d.get("total_return"), "pct"), _fm(d.get("mktcap"), "bi"),
        _fm(d.get("adtv"), "mi"), _fm(d.get("pe_26e"), "x"),
        _fm(d.get("ev_ebitda"), "x"), _fm(d.get("dl_ebitda"), "x"),
        _fm(d.get("roe"), "pct1"), _fm(d.get("roic"), "pct1"),
    ]) for tk, d in ordem)
    l2 = "".join(_linha(tk, d, [
        _fm(d.get("receita_26e"), "bi"), _fm(d.get("receita_27e"), "bi"),
        _fm(d.get("ebitda_26e"), "bi"), _fm(d.get("ebitda_27e"), "bi"),
        _fm(d.get("lucro_26e"), "bi"), _fm(d.get("lucro_27e"), "bi"),
    ]) for tk, d in ordem)

    h1 = "".join(f'<th style="{th}">{c}</th>' for c in
                 ["Preço", "Alvo", "Ret.", "Mkt Cap", "ADTV", "P/E 26E",
                  "EV/EBITDA", "DL/EBITDA", "ROE", "ROIC"])
    h2 = "".join(f'<th style="{th}">{c}</th>' for c in
                 ["Rec 26E", "Rec 27E", "EBITDA 26E", "EBITDA 27E",
                  "Lucro 26E", "Lucro 27E"])
    fontes = {f.split()[0] for d in dados.values() for f in d.get("_fontes", {}).values()}
    rotulo_fonte = " + ".join(sorted(fontes)) if fontes else "?"

    return f"""
    <p style="font-family:Arial;font-size:13px;font-weight:bold;color:#111;margin:14px 0 4px 0;">
      Valuation — cobertura</p>
    <div style="overflow-x:auto;">
    <table style="border-collapse:collapse;margin:0 0 2px 0;">
      <tr><th style="{thl}">Empresa</th>{h1}</tr>{l1}
    </table>
    <table style="border-collapse:collapse;margin:8px 0 2px 0;">
      <tr><th style="{thl}">Empresa</th>{h2}</tr>{l2}
    </table>
    </div>
    <p style="font-family:Arial;font-size:10px;color:#888;margin:4px 0 16px 0;">
      Mkt Cap/Rec/EBITDA/Lucro em bi; ADTV em mi (3m). Ret. = upside até o alvo + div. yield.
      * = ADR: preço/alvo/mktcap/ADTV em USD, fundamentos em BRL.
      EV/EBITDA, DL/EBITDA, ROE e ROIC: últimos 12m. 26E/27E: consenso ({rotulo_fonte}).
      "–" = sem dado na fonte.</p>
    """
