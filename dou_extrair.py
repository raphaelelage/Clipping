"""Transforma os atos brutos do MEC (coletados por dou_historico.py) no arquivo final:
uma linha por CURSO afetado, com todos os metadados que o ato traz.

COMO OS DADOS APARECEM NOS ATOS (estudado em portarias reais):
- A maioria das portarias da SERES lista os cursos numa TABELA com cabecalho na primeira
  linha: "Nº DE ORDEM | PROCESSO | CURSO | Nº DE VAGAS TOTAIS ANUAIS | MANTIDA | MANTENEDORA"
  (autorizacoes trazem tambem MUNICIPIO/UF; os nomes variam um pouco entre os anos).
- Credenciamento de IES sai pelo GABINETE DO MINISTRO, tambem com tabela ou em texto corrido.
- Atos de supervisao (cautelar, sancionador, sobrestamento) costumam ser texto corrido, com a
  IES e o codigo e-MEC no proprio paragrafo: "Faculdade X (cód. 4198)".
- Referencia judicial aparece como "Mandado de Segurança nº 1021315-57.2018.4.01.3400" ou
  "decisão judicial ... processo nº ...".

REGRA ANTI-OVERLAP: a chave de uma linha e (processo e-MEC) quando existir, senao
(nº portaria + curso + IES). O mesmo curso pode aparecer em VARIOS atos ao longo dos anos
(autorizacao -> reconhecimento -> renovacao): isso NAO e duplicata, e a historia do curso.
Duplicata e o mesmo processo no mesmo ato aparecendo 2x (ex.: retificacao) — mantemos a
publicacao mais recente e marcamos 'retificado'.
"""
import io
import json
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

import dou_historico as dh

# ---------------------------------------------------------------- classificacao do ato
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


# ordem importa: o primeiro padrao que casar define o tipo
TIPOS = [
    ("certificacao_cebas",       r"entidade[s]? beneficente|\bcebas\b"),
    ("aditamento_aumento_vagas", r"aumento d[eo] vagas|aditamento.*vagas"),
    ("reducao_vagas",            r"reducao d[eo] vagas"),
    ("autorizacao",              r"\bautoriza(?:cao|r|m)?\b.*curso|curso.*autorizad"),
    ("renovacao_reconhecimento", r"renovacao d[eo] reconhecimento"),
    ("reconhecimento",           r"\breconhecimento\b.*curso|curso.*reconhecid"),
    ("descredenciamento",        r"descredencia"),
    ("recredenciamento",         r"recredencia"),
    ("credenciamento",           r"\bcredencia"),
    ("medida_cautelar",          r"medida cautelar|cautelarmente"),
    ("sancionador_supervisao",   r"procedimento sancionador|supervisao|penalidade"),
    ("sobrestamento",            r"sobrest"),
    ("desativacao",              r"desativacao|extincao d[eo] curso"),
    ("chamamento_mais_medicos",  r"chamamento publico|mais medicos"),
]
_TIPOS_RX = [(t, re.compile(rx)) for t, rx in TIPOS]

# um ato do MEC so nos interessa se falar de regulacao de curso/IES
_RELEVANTE_RX = re.compile(
    r"credencia|autoriza|reconhec|vagas|curso superior|curso de |cautelar|sancionador|"
    r"supervisao|sobrest|desativa|chamamento|e-?mec")

RX_PROCESSO = re.compile(r"\b(20\d{7}|23[0-9.]{12,22}/\d{4}-\d{2})\b")
RX_COD_IES = re.compile(r"\(c[oó]d\.?\s*(\d{3,6})\)", re.I)
RX_JUDICIAL = re.compile(
    r"(mandado de seguran[çc]a|a[çc][ãa]o ordin[áa]ria|decis[ãa]o judicial|tutela|liminar|"
    r"adc\s*n?[ºo°]?\s*81)[^.;]{0,120}", re.I)
RX_PORTARIA_NUM = re.compile(r"PORTARIA[^\d]{0,40}N[ºo°]?\s*([\d.]+)\s*,?\s*DE\s+(.{5,40}?\d{4})", re.I)


def classificar(titulo, texto):
    t = _norm(titulo) + " " + _norm(texto[:2500])
    for tipo, rx in _TIPOS_RX:
        if rx.search(t):
            return tipo
    return "outro"


def relevante(a, texto=""):
    h = a.get("hierarchyStr", "")
    if "Regulação e Supervisão da Educação Superior" in h:
        return True                     # SERES: tudo interessa
    blob = _norm(a.get("title", "")) + " " + _norm(a.get("content", "") or texto[:1500])
    if "Conselho Nacional de Educaç" in h:
        return bool(_RELEVANTE_RX.search(blob))
    # Gabinete do Ministro assina credenciamento, mas tambem nomeacao/exoneracao (ruido)
    if h.rstrip("/").endswith("Gabinete do Ministro") or h.rstrip("/").endswith("Gabinete"):
        return bool(_RELEVANTE_RX.search(blob))
    return False


# ---------------------------------------------------------------- tabelas -> linhas
# Chaves COMPACTAS (so letras) porque o DOU varia espacos e pontuacao entre anos:
# "Registro e-MEC no", "REGISTRO E-MEC Nº", "Registroe-MEC no" sao a mesma coluna.
# Levantado dos cabecalhos reais de mar/2018: a coluna de processo se chama
# "Registro e-MEC nº" na maioria das tabelas — nao "PROCESSO".
_MAPA_COL = {
    "nodeordem": "_ordem", "noordem": "_ordem", "ordem": "_ordem",
    "processo": "processo_emec", "processoemec": "processo_emec",
    "registroemecno": "processo_emec", "registroemec": "processo_emec",
    "noemec": "processo_emec", "nodoprocessoemec": "processo_emec",
    "curso": "curso", "cursos": "curso", "cursograu": "curso",
    "vagas": "vagas", "nodevagastotaisanuais": "vagas", "vagastotaisanuais": "vagas",
    "nodevagas": "vagas", "numerodevagas": "vagas", "vagastotais": "vagas",
    "mantida": "ies", "ies": "ies", "iessigla": "ies", "instituicao": "ies",
    "instituicaodeeducacaosuperior": "ies",
    "mantenedora": "mantenedora", "mantenedoracnpj": "mantenedora",
    "municipio": "municipio", "municipiouf": "municipio",
    "uf": "uf",
    "endereco": "endereco", "enderecodefuncionamentodocurso": "endereco",
    "enderecodeofertadocurso": "endereco", "localdeoferta": "endereco",
    # NAO mapeados de proposito: "nomedaentidade"/"cnpj"/"nodoprocesso" (tabelas CEBAS,
    # certificacao de filantropia — nao e regulacao de curso) e "pesquisador" (bolsas).
}

# municipio/UF a partir do endereco de funcionamento: "... - Camobi - Santa Maria - RS"
RX_MUN_UF = re.compile(r"[-–,/]\s*([A-ZÀ-Ú][^-–,/]{2,40}?)\s*[-–,/]\s*([A-Z]{2})\s*\.?\s*$")


def _mapear_colunas(row):
    return [_MAPA_COL.get(re.sub(r"[^a-z]", "", _norm(c)), None) for c in row]


def linhas_da_tabela(html):
    """Explode as tabelas do ato em linhas por curso. Cabecalho vem na PRIMEIRA LINHA de
    dados (as tabelas do DOU nao usam <th>). Tabela sem coluna reconhecivel e ignorada —
    e o rodape de estatisticas do site, nao dados."""
    try:
        tabelas = pd.read_html(io.StringIO(html))
    except Exception:
        return []
    out = []
    for t in tabelas:
        if t.empty or len(t) < 2:
            continue
        cab = _mapear_colunas(t.iloc[0].tolist())
        if not any(c in ("processo_emec", "curso", "ies") for c in cab):
            continue
        for _, row in t.iloc[1:].iterrows():
            d = {}
            for col, val in zip(cab, row.tolist()):
                if col and col != "_ordem" and pd.notna(val):
                    d[col] = str(val).strip()
            if d.get("endereco") and not d.get("municipio"):
                m = RX_MUN_UF.search(d["endereco"])
                if m:
                    d["municipio"], d["uf"] = m.group(1).strip(), m.group(2)
            if d.get("curso") or d.get("processo_emec") or d.get("ies"):
                out.append(d)
    return out


def _so_numero_vagas(v):
    m = re.search(r"\d+", str(v or "").replace(".", ""))
    return int(m.group()) if m else None


# ---------------------------------------------------------------- pipeline
def extrair(atos, workers=6, log=print):
    """De cada ato relevante: baixa o texto integral, classifica e explode em linhas."""
    candidatos = [a for a in atos if relevante(a)]
    log(f"[extrair] {len(candidatos)} atos relevantes de {len(atos)} do MEC")

    linhas, sem_texto = [], 0
    feito = 0

    def um(a):
        texto, html = dh.texto_integral(a["urlTitle"])
        return a, texto, html

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(um, a) for a in candidatos]
        for fut in as_completed(futs):
            a, texto, html = fut.result()
            feito += 1
            if feito % 200 == 0:
                log(f"[extrair] {feito}/{len(candidatos)} atos | {len(linhas)} linhas")
            if not texto:
                sem_texto += 1
                texto = a.get("content", "") or ""
                html = ""
            tipo = classificar(a.get("title", ""), texto)
            jud = RX_JUDICIAL.search(texto)
            base = {
                "data_publicacao": a.get("pubDate", ""),
                "secao": a.get("_secao", "do1"),
                "orgao": a.get("hierarchyStr", ""),
                "ato": a.get("title", "").strip(),
                "tipo_ato": tipo,
                "artType": a.get("artType", ""),
                "pagina": a.get("numberPage", ""),
                "edicao": a.get("editionNumber", ""),
                "ref_judicial": jud.group(0).strip()[:160] if jud else "",
                "link": "https://www.in.gov.br/web/dou/-/" + str(a.get("urlTitle", "")).lstrip("/"),
                "retificacao": "(*)" in a.get("title", ""),
            }
            cursos = linhas_da_tabela(html) if html else []
            if cursos:
                for c in cursos:
                    linhas.append({**base, **c,
                                   "vagas_num": _so_numero_vagas(c.get("vagas")),
                                   "fonte_detalhe": "tabela do ato"})
            else:
                # texto corrido: extrai o que der do proprio paragrafo
                proc = RX_PROCESSO.search(texto)
                cod = RX_COD_IES.search(texto)
                linhas.append({**base,
                               "processo_emec": proc.group(0) if proc else "",
                               "cod_ies": cod.group(1) if cod else "",
                               "resumo_texto": texto[:400],
                               "fonte_detalhe": "texto corrido"})
    log(f"[extrair] FIM: {len(linhas)} linhas | {sem_texto} atos sem texto integral "
        f"(usado o resumo da listagem)")
    return pd.DataFrame(linhas)


if __name__ == "__main__":
    entrada = sys.argv[1]
    with io.open(entrada, encoding="utf-8") as fh:
        atos = json.load(fh)["atos"]
    df = extrair(atos)
    saida = entrada.replace(".json", "_linhas.parquet")
    df.to_parquet(saida)
    print(f"[ok] {len(df)} linhas -> {saida}")
