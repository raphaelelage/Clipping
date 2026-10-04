# -*- coding: utf-8 -*-
"""O rascunho de WhatsApp do Blast, com os numeros preenchidos.

O dono escreve o texto uma vez, marcando onde o numero entra; o robo substitui.
A marca e uma chave entre `{}` com tres informacoes, em qualquer ordem:

    {SULA net_adds QTD}      net adds da SULA no trimestre em curso, em milhares
    {Market lives}           vidas do mercado no mes de referencia (milhares)
    {HAPV yoy}               Base Growth YoY, em %
    {odonto ODPV net_adds}   a mesma coisa, na secao odontologica
    {rotulo Mês}             "Aug-26" — o rotulo, nao o numero

    metricas   lives · net_adds (padrao) · growth · mom · yoy · rotulo
    periodos   Mês (padrao) · QTD · Trimestre · YTD · Ano
    secoes     medico (padrao) · odonto · corporate · corporate_odonto
    extras     abs (vidas em vez de milhares) · mod (sem o sinal de menos)

O que NAO e reconhecido como metrica, periodo, secao ou extra e tratado como
nome do grupo — por isso "Porto Seguro" e "Unimed Seguros" funcionam sem aspas.

Os numeros saem de preferencia da tabela JA MONTADA, nao de uma segunda conta.
Isso e deliberado: o texto e o print da tabela vao juntos no WhatsApp, e duas
rotinas de calculo acabariam discordando em algum arredondamento. So quando o
periodo pedido nao e nenhuma das colunas do mes e que o valor e calculado — e
mesmo assim pelos metodos da mesma `Serie` que a tabela usou.

Tres modelos, um para cada posicao do mes dentro do trimestre (1o, 2o, 3o): no
3o mes o trimestre fechou e o texto fala dele; antes disso fala do mes e do
acumulado parcial. `escolher(mes)` decide qual vai.
"""
from __future__ import annotations

import io
import json
import os
import re
import unicodedata

import blast_periodos as bp
import blast_render as br

AQUI = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(AQUI, "blast_textos.json")

MARCA = re.compile(r"\{([^{}\n]+)\}")

# ------------------------------------------------------------------ aliases
LIVES, NET, GROWTH, MOM, YOY, ROTULO = ("lives", "net_adds", "growth",
                                        "mom", "yoy", "rotulo")
_METRICAS = {
    "lives": LIVES, "vidas": LIVES, "base": LIVES,
    "net_adds": NET, "netadds": NET, "net": NET, "na": NET, "adds": NET,
    "growth": GROWTH, "cresc": GROWTH, "crescimento": GROWTH,
    "mom": MOM, "yoy": YOY,
    "rotulo": ROTULO, "label": ROTULO, "mes_rotulo": ROTULO,
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
_EXTRAS = {"abs", "mod"}
# tipo do periodo concreto, para casar a chave pedida com as colunas da tabela
_TIPO = {bp.MES: "mes", bp.QTD: "qtd", bp.TRI: "trimestre",
         bp.YTD: "ytd", bp.ANO: "ano"}


def _slug(txt: str) -> str:
    """minusculas, sem acento, sem pontuacao — para casar nome digitado a dedo."""
    n = unicodedata.normalize("NFKD", str(txt))
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9_]+", "_", n.lower()).strip("_")


# ------------------------------------------------------------------ parse
def _parse(expr: str) -> dict:
    """`"odonto ODPV net_adds QTD mod"` -> as partes."""
    met = per = sec = None
    extras, nome = set(), []
    for tok in expr.split():
        s = _slug(tok)
        if s in _METRICAS and met is None:
            met = _METRICAS[s]
        elif s in _PERIODOS and per is None:
            per = _PERIODOS[s]
        elif s in _SECOES and sec is None:
            sec = _SECOES[s]
        elif s in _EXTRAS:
            extras.add(s)
        else:
            nome.append(tok)
    return {"metrica": met or NET, "periodo": per or bp.MES,
            "secao": sec or "medico", "extras": extras,
            "grupo": " ".join(nome).strip(), "bruto": expr.strip()}


def _rotulo(chave: str, ano: int, mes: int) -> str:
    if chave == bp.MES:
        return bp.rotulo_mes(ano, mes)
    if chave == bp.QTD:
        # o trimestre EM CURSO (o `resolver` do QTD devolve so "QTD")
        return bp.rotulo_tri(ano, (mes - 1) // 3 + 1)
    if chave == bp.YTD:
        return f"YTD {ano}"
    p = bp.resolver(chave, ano, mes)
    return p["rotulo"] if p else chave


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
            self.secoes[t["secao"]] = {
                "t": t,
                "linhas": {_slug(l["rotulo"]): l for l in t["linhas"]},
            }
            ano = ano or t["ano"]
            mes = mes or t["mes"]
        self.ano, self.mes = ano, mes

    # ---- valores
    def _coluna(self, t, chave):
        tipo = _TIPO.get(chave)
        for i, p in enumerate(t["periodos"]):
            if p["tipo"] == tipo:
                return i
        return None

    def valor(self, p: dict):
        """(valor, aviso). Valor em milhares para lives/net_adds; fracao para %."""
        if p["metrica"] == ROTULO:
            return _rotulo(p["periodo"], self.ano, self.mes), None

        sec = self.secoes.get(p["secao"])
        if sec is None:
            return None, f"seção “{p['secao']}” não foi montada"
        if not p["grupo"]:
            return None, f"“{p['bruto']}”: falta o nome do grupo"
        linha = sec["linhas"].get(_slug(p["grupo"]))
        if linha is None:
            return None, (f"“{p['grupo']}” não é uma linha da seção "
                          f"{p['secao']} (veja a aba Grupos)")

        if p["metrica"] == LIVES:
            return linha["lives"], None
        if p["metrica"] == MOM:
            return linha["mom"], None
        if p["metrica"] == YOY:
            return linha["yoy"], None

        # net adds / growth: coluna da tabela quando existe, senao calcula
        periodo = bp.resolver(p["periodo"], self.ano, self.mes)
        i = self._coluna(sec["t"], p["periodo"])
        if p["metrica"] == NET and i is not None:
            return linha["net_adds"][i], None
        return self._calcular(p, linha, periodo)

    def _calcular(self, p, linha, periodo):
        regs = linha.get("registros") or []
        if not regs:
            # "Others" e residual (Market menos os grupos): nao tem registro
            # proprio, entao fora das colunas do mes nao da para recalcular
            return None, (f"“{p['bruto']}”: esta linha é residual e este período "
                          f"não é coluna do mês — não tem como calcular")
        par = self.series.get(p["secao"])
        serie = None
        if par:
            serie = par[1] if regs == ["MERCADO"] else par[0]
        if serie is None:
            return None, (f"“{p['bruto']}”: período fora das colunas do mês e "
                          f"sem a série para calcular")
        if p["metrica"] == GROWTH:
            return serie.crescimento(regs, periodo), None
        v = serie.net_adds(regs, periodo)
        return (None if v is None else v / 1000.0), None


# ------------------------------------------------------------------ aplicar
def _formatar(valor, p: dict) -> str:
    if valor is None:
        return "n.a."
    if isinstance(valor, str):
        return valor
    if p["metrica"] in (MOM, YOY, GROWTH):
        return br.fmt_pct(valor)
    if "mod" in p["extras"]:
        valor = abs(valor)
    if "abs" in p["extras"]:
        valor = valor * 1000.0
    return br.fmt_milhares(valor)


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
        return _formatar(valor, p)

    return MARCA.sub(_troca, modelo or ""), avisos


# ------------------------------------------------------------------ validacao
def validar(modelo: str, grupos: dict | None = None) -> list[str]:
    """Confere as marcas SEM precisar de dado — e o que o app usa para avisar
    antes de salvar. Checa metrica, periodo e se o grupo existe no layout."""
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

    problemas = []
    for m in MARCA.finditer(modelo or ""):
        p = _parse(m.group(1))
        if p["metrica"] == ROTULO:
            continue
        if not p["grupo"]:
            problemas.append(f"«{p['bruto']}» — falta o nome do grupo")
            continue
        base = ("odonto" if p["secao"].endswith("odonto") else "medico")
        alvo = rot.get(base)
        if alvo and _slug(p["grupo"]) not in alvo:
            problemas.append(f"«{p['bruto']}» — “{p['grupo']}” não está no "
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

_M1 = """*ANS | Beneficiários {rotulo Mês}*

*Médico-hospitalar:* o setor fechou {rotulo Mês} com {Market lives} mil vidas, \
net adds de {Market net_adds Mês} mil no mês ({Market mom} MoM).
• HAPV: {HAPV net_adds Mês} mil ({HAPV mom} MoM)
• SULA: {SULA net_adds Mês} mil ({SULA mom} MoM)
• Amil: {Amil net_adds Mês} mil
• Bradesco: {Bradesco net_adds Mês} mil

*Odontológico:* {odonto Market net_adds Mês} mil no mês, base de \
{odonto Market lives} mil vidas.
• ODPV: {odonto ODPV net_adds Mês} mil ({odonto ODPV mom} MoM)
• HAPV: {odonto HAPV net_adds Mês} mil

No {rotulo QTD} em curso: SULA {SULA net_adds QTD} mil e HAPV \
{HAPV net_adds QTD} mil."""

_M2 = """*ANS | Beneficiários {rotulo Mês}*

*Médico-hospitalar:* net adds de {Market net_adds Mês} mil em {rotulo Mês} \
({Market mom} MoM); {Market net_adds QTD} mil no {rotulo QTD} até aqui.
• HAPV: {HAPV net_adds Mês} mil no mês · {HAPV net_adds QTD} mil no trimestre
• SULA: {SULA net_adds Mês} mil no mês · {SULA net_adds QTD} mil no trimestre
• Amil: {Amil net_adds Mês} mil no mês
• Bradesco: {Bradesco net_adds Mês} mil no mês

*Odontológico:* {odonto Market net_adds Mês} mil no mês.
• ODPV: {odonto ODPV net_adds Mês} mil · {odonto ODPV net_adds QTD} mil no trimestre
• HAPV: {odonto HAPV net_adds Mês} mil

Crescimento YoY: mercado {Market yoy}, HAPV {HAPV yoy}, SULA {SULA yoy}."""

_M3 = """*ANS | Beneficiários {rotulo Mês} — fechamento do {rotulo Trimestre}*

*Médico-hospitalar:* o setor somou {Market net_adds Trimestre} mil vidas no \
{rotulo Trimestre} ({Market net_adds Mês} mil em {rotulo Mês}), base de \
{Market lives} mil.
• HAPV: {HAPV net_adds Trimestre} mil no trimestre ({HAPV yoy} YoY)
• SULA: {SULA net_adds Trimestre} mil no trimestre ({SULA yoy} YoY)
• Amil: {Amil net_adds Trimestre} mil
• Bradesco: {Bradesco net_adds Trimestre} mil

*Odontológico:* {odonto Market net_adds Trimestre} mil no trimestre.
• ODPV: {odonto ODPV net_adds Trimestre} mil ({odonto ODPV yoy} YoY)
• HAPV: {odonto HAPV net_adds Trimestre} mil

*Corporate (Coletivo Empresarial):* {corporate Market net_adds Trimestre} mil \
no médico e {corporate_odonto Market net_adds Trimestre} mil no odonto."""

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
        print(f"--- modelo {k} ({POSICOES[k]}) ---")
        print(" · ".join(marcas(v)[:6]), "…")
