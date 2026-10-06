# -*- coding: utf-8 -*-
"""O rascunho de WhatsApp do Blast, com os numeros preenchidos.

O texto e do dono — os tres modelos abaixo sao, palavra por palavra, os que ele
escreveu (01/10/2026), com os numeros trocados por marcas. Ele edita a prosa no
app; o robo so substitui o que esta entre `{}`.

A marca leva metrica, periodo e grupo, em qualquer ordem:

    {SULA net_adds QTD}      net adds da SULA no trimestre em curso, em milhares
    {Market lives}           vidas do mercado no mes de referencia
    {HAPV yoy sinal}         Base Growth YoY com o sinal: "+1,4%"
    {ODPV verbo Mês | ganhou | perdeu}   a palavra que casa com o sinal
    {odonto ODPV net_adds}   a mesma coisa, na secao odontologica
    {rotulo Mês}             "Jul/26" — o rotulo, nao o numero

    metricas   lives · net_adds (padrao) · growth · mom · yoy · rotulo
    periodos   Mês (padrao) · QTD · Trimestre · YTD · Ano
    secoes     medico (padrao) · odonto · corporate · corporate_odonto
    extras     sinal (forca o + no positivo) · mod (tira o sinal) ·
               abs (vidas em vez de milhares) · en (rotulo em ingles)

VERBO existe porque o numero muda de sinal e a frase nao. "ODPV perdeu 66k" saiu
assim em Ago/26, quando a ODPV na verdade GANHOU 66 mil vidas: o `mod` escondia
o sinal e o verbo ficou mentindo. Com `{ODPV verbo Mês | ganhou | perdeu}` a
palavra acompanha o dado; sem as palavras, o padrao e ganhou/perdeu.

Nome repetido no layout (o grupo "Amil" tem uma sub-linha "Amil") resolve para o
GRUPO. Para falar da sub-linha, use o nome composto: `{Amil > Amil net_adds Mês}`.

O nome do grupo aceita CONTA, com espaco dos dois lados do operador:

    {HAPV - Hapvida - ND Intermédica net_adds Mês}

que e como sai o "em outras operadoras" do texto do 2o mes. Exigir o espaco e o
que impede "SulAmérica (ex. ASO)" e "Médico-hospitalar" de serem quebrados.

NUMERO EM PORTUGUES, ao contrario da tabela. A tabela e equity research em
ingles ("1,234" e "0.1%") por pedido do dono; o texto do WhatsApp e prosa em
portugues, e os exemplos dele dizem "+1,4% YoY" e "268k". Sao convencoes
diferentes de proposito — o que nao pode divergir e o VALOR, e ele sai da mesma
tabela que vira o print.

Tres modelos, um por posicao do mes no trimestre (no 3o o trimestre fechou e o
texto fala dele). `escolher(mes)` decide qual vai.
"""
from __future__ import annotations

import io
import json
import os
import re
import unicodedata

import blast_periodos as bp

AQUI = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(AQUI, "blast_textos.json")

MARCA = re.compile(r"\{([^{}\n]+)\}")
CONTA = re.compile(r"\s+([+-])\s+")        # operador com espaco dos dois lados

MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
            "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

# ------------------------------------------------------------------ aliases
LIVES, NET, GROWTH, MOM, YOY, ROTULO, VERBO = ("lives", "net_adds", "growth",
                                              "mom", "yoy", "rotulo", "verbo")
VERBO_PADRAO = ("ganhou", "perdeu")      # positivo, negativo
_METRICAS = {
    "lives": LIVES, "vidas": LIVES, "base": LIVES,
    "net_adds": NET, "netadds": NET, "net": NET, "na": NET, "adds": NET,
    "growth": GROWTH, "cresc": GROWTH, "crescimento": GROWTH,
    "mom": MOM, "yoy": YOY,
    "rotulo": ROTULO, "label": ROTULO,
    "verbo": VERBO,
}
_PERIODOS = {
    "mes": bp.MES, "m": bp.MES, "mensal": bp.MES,
    "qtd": bp.QTD,
    "trimestre": bp.TRI, "tri": bp.TRI, "quarter": bp.TRI, "q": bp.TRI,
    "ytd": bp.YTD,
    "ano": bp.ANO, "year": bp.ANO, "anual": bp.ANO,
}
_SECOES = {
    "medico": "medico", "medico_hospitalar": "medico", "saude": "medico",
    "health": "medico", "hospitalar": "medico",
    "odonto": "odonto", "odontologico": "odonto", "dental": "odonto",
    "corporate": "corporate_medico", "corporate_medico": "corporate_medico",
    "corporativo": "corporate_medico",
    "corporate_odonto": "corporate_odonto", "corporate_dental": "corporate_odonto",
}
_EXTRAS = {"abs", "mod", "sinal", "en"}
_TIPO = {bp.MES: "mes", bp.QTD: "qtd", bp.TRI: "trimestre",
         bp.YTD: "ytd", bp.ANO: "ano"}


def _slug(txt: str) -> str:
    """minusculas, sem acento, sem pontuacao — para casar nome digitado a dedo."""
    n = unicodedata.normalize("NFKD", str(txt))
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9_]+", "_", n.lower()).strip("_")


# ------------------------------------------------------------------ parse
def _parse(expr: str) -> dict:
    # `{HAPV verbo Mês | ganhou | perdeu}` — depois da primeira barra vem a
    # palavra do positivo e a do negativo
    corpo, *palavras = [x.strip() for x in expr.split("|")]
    met = per = sec = None
    extras, nome = set(), []
    for tok in corpo.split():
        s = _slug(tok)
        if s in _METRICAS and met is None:
            met = _METRICAS[s]
        elif s in _PERIODOS and per is None:
            per = _PERIODOS[s]
        elif s in _SECOES and sec is None:
            sec = _SECOES[s]
        elif s in _EXTRAS:
            extras.add(s)
        elif tok in "+-" and nome:
            nome.append(tok)           # operador da conta: so vale entre nomes
        else:
            nome.append(tok)
    return {"metrica": met or NET, "periodo": per or bp.MES,
            "secao": sec or "medico", "extras": extras,
            "palavras": [w for w in palavras if w],
            "grupo": " ".join(nome).strip(" +-"), "bruto": expr.strip()}


def termos(nome: str) -> list[tuple[int, str]]:
    """`"HAPV - Hapvida - ND Intermédica"` -> [(+1,'HAPV'), (-1,'Hapvida'), ...]."""
    partes = CONTA.split(nome)
    out, sinal = [], 1
    for i, p in enumerate(partes):
        if i % 2:                       # posicoes impares sao os operadores
            sinal = -1 if p == "-" else 1
            continue
        p = p.strip()
        if p:
            out.append((sinal, p))
    return out


# ------------------------------------------------------------------ formato
def _rotulo(chave: str, ano: int, mes: int, ingles: bool = False) -> str:
    if chave == bp.MES:
        return (bp.rotulo_mes(ano, mes) if ingles
                else f"{MESES_PT[mes - 1]}/{ano % 100:02d}")
    if chave == bp.QTD:                 # o trimestre EM CURSO
        t = (mes - 1) // 3 + 1
        return (bp.rotulo_tri(ano, t) if ingles else f"{t}T{ano % 100:02d}")
    if chave == bp.YTD:
        return f"YTD {ano}"
    p = bp.resolver(chave, ano, mes)
    if not p:
        return chave
    if p["tipo"] == "trimestre" and not ingles:
        return f"{p['tri']}T{p['ano'] % 100:02d}"
    return p["rotulo"]


def _milhar_pt(v: float) -> str:
    """Padrao brasileiro: ponto no milhar. Sem decimal — o texto fala em "k"."""
    if round(v) == 0:
        v = 0                           # evita "-0" quando arredonda para zero
    return f"{v:,.0f}".replace(",", ".")


def _formatar(valor, p: dict) -> str:
    if p["metrica"] == VERBO:
        if valor is None:
            return "n.a."
        pos, neg = (list(p["palavras"]) + list(VERBO_PADRAO))[:2]
        return pos if valor >= 0 else neg
    if valor is None:
        return "n.a."
    if isinstance(valor, str):
        return valor
    extras = p["extras"]
    if "mod" in extras:
        valor = abs(valor)
    if p["metrica"] in (MOM, YOY, GROWTH):
        s = f"{valor * 100:.1f}".replace(".", ",") + "%"
    else:
        if "abs" in extras:
            valor = valor * 1000.0
        s = _milhar_pt(valor)
    return "+" + s if ("sinal" in extras and valor > 0) else s


def _indexar(linhas) -> dict:
    """Nome -> linha, com o GRUPO ganhando da sub-linha de mesmo nome.

    O layout repete nome de proposito: o grupo "Amil" tem uma sub-linha "Amil"
    (a operadora, sem o residual do grupo), e "Others" aparece dentro de varios
    grupos e tambem como linha de topo. Indexar na ordem fazia a ultima vencer,
    e `{Amil}` devolvia 21k em vez dos 23k do grupo — calado, que e o pior jeito
    de errar (visto em 04/10/2026, comparando com o texto do dono).

    Sub-linha de nome repetido continua enderecavel pelo nome composto:
    `{Amil > Amil net_adds Mês}`.
    """
    por_nome, topo = {}, None
    for l in linhas:
        chave = _slug(l["rotulo"])
        if l["nivel"] == 0:
            topo = l["rotulo"]
            por_nome[chave] = l              # topo sempre vence
        else:
            por_nome.setdefault(chave, l)    # sub so ocupa nome ainda livre
            por_nome[_slug(f"{topo} > {l['rotulo']}")] = l
    return por_nome


# ------------------------------------------------------------------ contexto
class Contexto:
    """De onde os numeros vem: as tabelas montadas e, em segundo plano, as series.

    `series[secao] = (serie, serie_mercado)` — as mesmas instancias que a tabela
    usou, so para periodos que nao sao coluna do mes.
    """

    def __init__(self, tabelas, series=None, ano=None, mes=None):
        self.series = series or {}
        self.secoes = {}
        for t, _titulo in tabelas:
            self.secoes[t["secao"]] = {"t": t, "linhas": _indexar(t["linhas"])}
            ano = ano or t["ano"]
            mes = mes or t["mes"]
        self.ano, self.mes = ano, mes

    def _coluna(self, t, chave):
        tipo = _TIPO.get(chave)
        for i, pr in enumerate(t["periodos"]):
            if pr["tipo"] == tipo:
                return i
        return None

    def valor(self, p: dict):
        """(valor, aviso). Milhares para lives/net_adds; fracao para %."""
        if p["metrica"] == ROTULO:
            return _rotulo(p["periodo"], self.ano, self.mes,
                           "en" in p["extras"]), None
        if not p["grupo"]:
            return None, f"“{p['bruto']}”: falta o nome do grupo"
        if p["secao"] not in self.secoes:
            return None, f"seção “{p['secao']}” não foi montada"

        parcelas = termos(p["grupo"])
        if len(parcelas) > 1 and p["metrica"] in (MOM, YOY, GROWTH):
            return None, (f"“{p['bruto']}”: conta de grupos só vale para vidas e "
                          f"net adds — percentual não se soma")
        total = 0.0
        for sinal, nome in parcelas:
            v, aviso = self._um(p, nome)
            if aviso:
                return None, aviso
            if v is None:
                return None, None
            total += sinal * v
        return total, None

    def _um(self, p: dict, nome: str):
        sec = self.secoes[p["secao"]]
        linha = sec["linhas"].get(_slug(nome))
        if linha is None:
            return None, (f"“{nome}” não é uma linha da seção {p['secao']} "
                          f"(veja a aba Grupos)")
        if p["metrica"] == LIVES:
            return linha["lives"], None
        if p["metrica"] == MOM:
            return linha["mom"], None
        if p["metrica"] == YOY:
            return linha["yoy"], None

        periodo = bp.resolver(p["periodo"], self.ano, self.mes)
        i = self._coluna(sec["t"], p["periodo"])
        if p["metrica"] in (NET, VERBO) and i is not None:
            return linha["net_adds"][i], None
        return self._calcular(p, nome, linha, periodo)

    def _calcular(self, p, nome, linha, periodo):
        regs = linha.get("registros") or []
        if not regs:
            # "Others" e residual (Market menos os grupos): nao tem registro
            # proprio, entao fora das colunas do mes nao da para recalcular
            return None, (f"“{nome}” é linha residual e este período não é "
                          f"coluna do mês — não tem como calcular")
        par = self.series.get(p["secao"])
        serie = None
        if par:
            serie = par[1] if regs == ["MERCADO"] else par[0]
        if serie is None:
            return None, (f"“{p['bruto']}”: período fora das colunas do mês e "
                          f"sem a série para calcular")
        if p["metrica"] == GROWTH:
            return serie.crescimento(regs, periodo), None
        v = serie.net_adds(regs, periodo)      # tambem serve ao VERBO
        return (None if v is None else v / 1000.0), None


# ------------------------------------------------------------------ aplicar
def _tem_verbo_irmao(modelo: str, p: dict) -> bool:
    """O aviso do `mod` so vale quando o verbo da frase e FIXO. Se o mesmo grupo
    ja aparece com uma marca de verbo no texto, a frase se ajusta sozinha."""
    alvo = _slug(p["grupo"])
    for m in MARCA.finditer(modelo or ""):
        q = _parse(m.group(1))
        if q["metrica"] == VERBO and _slug(q["grupo"]) == alvo:
            return True
    return False


def aplicar(modelo: str, ctx: Contexto) -> tuple[str, list[str]]:
    """Troca as marcas pelos numeros. Devolve (texto, avisos).

    Marca que nao resolve NAO desaparece: fica como `«...»` no texto. Sumir em
    silencio seria pior — o dono mandaria o recado sem o numero sem perceber.
    """
    avisos = []

    def _troca(m):
        p = _parse(m.group(1))
        valor, aviso = ctx.valor(p)
        if aviso:
            avisos.append(aviso)
            return f"«{p['bruto']}»"
        # `mod` existe para escrever "perdeu 58k" sobre um valor NEGATIVO. Se o
        # valor virou positivo, o numero sai certo e o VERBO fica mentindo — e
        # ninguem percebe, porque o sinal foi escondido de proposito. Avisar e
        # a unica defesa: a prosa e do dono, o robo nao reescreve.
        if ("mod" in p["extras"] and p["metrica"] != VERBO
                and isinstance(valor, (int, float)) and valor > 0
                and not _tem_verbo_irmao(modelo, p)):
            avisos.append(f"“{p['bruto']}” esconde o sinal (mod) e o valor "
                          f"virou POSITIVO — confira o verbo da frase")
        return _formatar(valor, p)

    return MARCA.sub(_troca, modelo or ""), avisos


# ------------------------------------------------------------------ validacao
def validar(modelo: str, grupos: dict | None = None) -> list[str]:
    """Confere as marcas SEM precisar de dado — e o que o app usa para avisar
    antes de salvar. Checa se cada grupo citado existe no layout."""
    import blast_tabela as bt
    try:
        grupos = grupos or bt.carregar_grupos()
    except Exception:                                             # noqa: BLE001
        grupos = {}
    rot = {}
    for sec, cfg in (grupos or {}).items():
        if not isinstance(cfg, dict) or "layout" not in cfg:
            continue
        rot[sec] = {_slug(b["rotulo"]) for b in cfg["layout"]}
        rot[sec] |= {_slug(i["rotulo"]) for b in cfg["layout"]
                     for i in b.get("itens", [])}
        rot[sec] |= {_slug(f"{b['rotulo']} > {i['rotulo']}")
                     for b in cfg["layout"] for i in b.get("itens", [])}

    problemas = []
    for m in MARCA.finditer(modelo or ""):
        p = _parse(m.group(1))
        if p["metrica"] == ROTULO:
            continue
        if not p["grupo"]:
            problemas.append(f"«{p['bruto']}» — falta o nome do grupo")
            continue
        base = "odonto" if p["secao"].endswith("odonto") else "medico"
        alvo = rot.get(base)
        if not alvo:
            continue
        for _sinal, nome in termos(p["grupo"]):
            if _slug(nome) not in alvo:
                problemas.append(f"«{p['bruto']}» — “{nome}” não está no "
                                 f"layout de {base}")
    return problemas


def marcas(modelo: str) -> list[str]:
    return [m.group(1).strip() for m in MARCA.finditer(modelo or "")]


# ------------------------------------------------------------------ modelos
def escolher(mes: int) -> str:
    """Qual dos tres modelos vai, pela posicao do mes no trimestre."""
    return str((mes - 1) % 3 + 1)


POSICOES = {"1": "1º mês do trimestre", "2": "2º mês do trimestre",
            "3": "3º mês — o trimestre fechou"}

# Os tres textos sao do dono (01/10/2026), preservados ao pe da letra. O `*` e a
# formatacao do WhatsApp, que o proprio chat converte em negrito — por isso fica
# no texto e nao vira HTML.
_M1 = """Bom dia!

"Saíram os dados de beneficiários da ANS de {rotulo Mês}."

- O mercado de planos de saúde {Market verbo Mês | cresceu | encolheu} {Market net_adds Mês mod}k vidas ({Market yoy sinal} YoY);

- HAPV {HAPV verbo Mês | ganhou | perdeu} {HAPV net_adds Mês mod}k vidas no mês, sendo {Hapvida net_adds Mês sinal}k na Hapvida e {ND Intermédica net_adds Mês sinal}k na NDI;

- SULA {SulAmérica (ex. ASO) verbo Mês | adicionou | perdeu} {SulAmérica (ex. ASO) net_adds Mês mod}k vidas ex. ASO no mês;

- Bradesco manteve o ritmo de crescimento, com {Bradesco (ex. ASO) net_adds Mês sinal}k ex. ASO. no mês;

- Amil {Amil verbo Mês | adicionou | perdeu} {Amil net_adds Mês mod}k vidas no mês;

- Porto {Porto Seguro verbo Mês | adicionou | perdeu} {Porto Seguro net_adds Mês mod}k vidas no mês;

- As adições líquidas de planos odontológicos totalizaram {odonto Market net_adds Mês sinal}k vidas no mês ({odonto Market yoy sinal} YoY);

- ODPV {odonto ODPV verbo Mês | adicionou | perdeu} {odonto ODPV net_adds Mês mod}k vidas no mês.

Qualquer dúvida, estamos à disposição."""

_M2 = """Bom dia!

"Saíram os dados de beneficiários da ANS de {rotulo Mês}."

- O mercado de planos de saúde {Market verbo Mês | ganhou | perdeu} {Market net_adds Mês mod}k vidas no mês ({Market yoy sinal} YoY);

- HAPV {HAPV verbo Mês | segue ganhando | continua a perder} vidas ({HAPV net_adds Mês sinal}k no mês e {HAPV net_adds QTD sinal}k QTD), sendo {ND Intermédica net_adds Mês sinal}k na NDI, {Hapvida net_adds Mês sinal}k na Hapvida e {HAPV - Hapvida - ND Intermédica net_adds Mês sinal}k em outras operadoras;

- SULA {SulAmérica (ex. ASO) verbo Mês | adicionou | perdeu} {SulAmérica (ex. ASO) net_adds Mês mod}k vidas ex. ASO no mês ({SulAmérica (ex. ASO) net_adds QTD sinal}k QTD);

- Bradesco manteve o ritmo de crescimento, com {Bradesco (ex. ASO) net_adds Mês sinal}k ex ASO no mês ({Bradesco (ex. ASO) net_adds QTD sinal}k QTD).

- Amil cresceu fortemente, com {Amil net_adds Mês sinal}k vidas no mês e {Amil net_adds QTD sinal}k QTD;

- Porto {Porto Seguro verbo Mês | cresceu | caiu} {Porto Seguro net_adds Mês mod}k vidas no mês ({Porto Seguro net_adds QTD sinal}k QTD);

- Os planos odontológicos apresentaram {odonto Market verbo Mês | uma forte expansão de | uma retração de} {odonto Market net_adds Mês mod}k vidas no mês ({odonto Market net_adds QTD sinal}k QTD);

- ODPV {odonto ODPV verbo Mês | ganhou | perdeu} {odonto ODPV net_adds Mês mod}k vidas no mês ({odonto ODPV net_adds QTD sinal}k QTD). Notamos que os dados de beneficiários passaram a ser consolidados na Mediservice.

Qualquer dúvida, estamos à disposição."""

_M3 = """Bom dia!

"Saíram os dados de beneficiários da ANS de {rotulo Mês}."

- O mercado de planos de saúde {Market verbo Mês | cresceu | encolheu} {Market net_adds Mês mod}k vidas ({Market yoy sinal} YoY). No {rotulo Trimestre} {Market verbo Trimestre | o crescimento foi de | a queda foi de} {Market net_adds Trimestre mod}k vidas;

- HAPV {HAPV verbo Mês | ganhou | perdeu} {HAPV net_adds Mês mod}k vidas no mês e {HAPV net_adds Trimestre sinal}k no {rotulo Trimestre}, sendo {ND Intermédica net_adds Mês sinal}k na NDI e {Hapvida net_adds Mês sinal}k na Hapvida;

- SULA {SulAmérica (ex. ASO) verbo Mês | adicionou | perdeu} {SulAmérica (ex. ASO) net_adds Mês mod}k vidas ex. ASO no mês e {SulAmérica (ex. ASO) net_adds Trimestre sinal}k no {rotulo Trimestre};

- Bradesco manteve o ritmo de crescimento, com {Bradesco (ex. ASO) net_adds Mês sinal}k no mês e {Bradesco (ex. ASO) net_adds Trimestre sinal}k no {rotulo Trimestre} ex. ASO;

- Amil {Amil verbo Mês | adicionou | perdeu} {Amil net_adds Mês mod}k vidas no mês e {Amil net_adds Trimestre sinal}k no {rotulo Trimestre};

- Porto {Porto Seguro verbo Mês | adicionou | perdeu} {Porto Seguro net_adds Mês mod}k vidas no mês e {Porto Seguro net_adds Trimestre sinal}k no {rotulo Trimestre};

- As adições líquidas de planos odontológicos totalizaram {odonto Market net_adds Mês sinal}k vidas no mês ({odonto Market yoy sinal} YoY) e {odonto Market net_adds Trimestre sinal}k no {rotulo Trimestre};

- ODPV {odonto ODPV verbo Mês | ganhou | perdeu} {odonto ODPV net_adds Mês mod}k vidas no mês, totalizando {odonto ODPV net_adds Trimestre sinal}k de adições líquidas no {rotulo Trimestre}.

Qualquer dúvida, estamos à disposição."""

MODELOS_PADRAO = {"1": _M1, "2": _M2, "3": _M3}


def carregar(caminho: str = CFG) -> dict:
    """Os tres modelos. Cai no padrao quando o arquivo nao existe ou esta torto —
    o e-mail nunca deixa de sair por causa do texto."""
    try:
        cfg = json.load(io.open(caminho, encoding="utf-8"))
        mods = cfg.get("modelos") or {}
    except Exception:                                             # noqa: BLE001
        mods = {}
    return {k: (mods.get(k) or MODELOS_PADRAO[k]) for k in ("1", "2", "3")}


def do_mes(mes: int, caminho: str = CFG) -> str:
    return carregar(caminho)[escolher(mes)]


if __name__ == "__main__":
    for k, v in MODELOS_PADRAO.items():
        print(f"--- modelo {k} ({POSICOES[k]}) · {len(marcas(v))} marcas ---")
        print("\n".join(validar(v)) or "ok")
