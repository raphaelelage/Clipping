"""Regenera cautelares_enamed_2026.json a partir dos LINKS das portarias no DOU —
100% deterministico, SEM IA (dono, 11/09/2026: eliminar a dependencia de sessao).

Quando o radar avisar "portaria NOVA citando Enamed fora do JSON", rode:

    python atualizar_cautelares.py <link1> <link2> ...
    (ou sem argumentos: re-valida os links que JA estao no JSON)

O que ele le de cada portaria (mesma tecnica do dou_extrair):
  - MEDIDAS: no texto dos artigos, as expressoes canonicas (suspensao de ingresso,
    reducao de vagas, veto a aumento de vagas, sobrestamento...);
  - CURSOS: da(s) tabela(s) anexas (cod e-MEC do curso, cod da IES, nome da IES).
Nada e inventado: curso sem codigo na tabela e reportado e fica FORA (conferir a mao).
O arquivo antigo vira .bak antes de gravar.
"""
import io
import json
import os
import re
import sys

import pandas as pd

import dou_historico as dh

BASE = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(BASE, "cautelares_enamed_2026.json")

# expressoes canonicas de medida cautelar — com FOLGA no meio, porque o texto real
# escreve "suspensao IMEDIATA de ingresso" e "suspensao ou impedimento da
# protocolizacao de processos regulatorios de aditamento de aumento de vagas"
MEDIDAS_RX = [
    ("suspensao de ingresso", r"suspens\w*[^.;]{0,40}ingresso"),
    ("veto a aumento de vagas",
     r"(?:suspens|veda|impedi)\w*[^.;]{0,90}aumento\s+de\s+vagas"),
    ("suspensao de FIES/financiamento",
     r"suspens\w*[^.;]{0,60}(?:FIES|[Ff]inanciamento [Ee]studantil)"),
    ("reducao de vagas", r"redu[cç][aã]o\s+d[eo][^.;]{0,30}vagas"),
    ("sobrestamento de processos", r"sobrestamento|sobrestar"),
    ("tutoria/protocolo de melhoria", r"protocolo\s+de\s+melhoria|tutoria"),
]
RX_NUM_PORTARIA = re.compile(r"PORTARIA[^\d]{0,40}N[ºo°]?\s*([\d.]+)", re.I)
RX_COD = re.compile(r"^\d{3,8}$")


def _limpa_html(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or ""))


def extrair_portaria(url):
    """(numero, medidas, cursos) de UMA portaria de cautelares do Enamed."""
    url_title = url.rstrip("/").split("/-/")[-1]
    texto, html = dh.texto_integral(url_title)
    if not texto:
        raise RuntimeError(f"sem texto em {url}")
    m = RX_NUM_PORTARIA.search(texto)
    numero = (m.group(1).replace(".", "") if m else "").strip()
    medidas = [nome for nome, rx in MEDIDAS_RX if re.search(rx, texto, re.I)]

    cursos, sem_codigo = [], 0
    try:
        tabelas = pd.read_html(io.StringIO(html))
    except ValueError:
        tabelas = []
    for t in tabelas:
        # cabecalho na 1a linha de dados (padrao das tabelas do DOU)
        t = t.astype(str)
        import unicodedata as _ud
        def _h(x):
            x = _ud.normalize("NFKD", str(x))
            x = "".join(c for c in x if not _ud.combining(c))
            return re.sub(r"\W+", "", x).lower()   # "Código do curso"->"codigodocurso"
        head = [_h(x) for x in t.iloc[0]]
        col = {}
        for j, h in enumerate(head):
            if "curso" in h and ("cod" in h or "emec" in h or "registro" in h):
                col["cod_curso"] = j
            elif h in ("codigodocurso", "ncurso"):
                col["cod_curso"] = j
            elif "ies" in h and "cod" in h:
                col["cod_ies"] = j
            elif h in ("ies", "instituicao", "nomedaies", "mantida"):
                col["ies"] = j
        if "cod_curso" not in col:
            continue
        for _, row in t.iloc[1:].iterrows():
            cod = re.sub(r"\D", "", str(row.iloc[col["cod_curso"]]))
            if not RX_COD.match(cod):
                sem_codigo += 1
                continue
            item = {"cod_curso": cod}
            if "cod_ies" in col:
                ci = re.sub(r"\D", "", str(row.iloc[col["cod_ies"]]))
                if ci:
                    item["cod_ies"] = ci
            if "ies" in col:
                item["ies"] = str(row.iloc[col["ies"]]).strip()
            cursos.append(item)
    if not cursos:
        # P.76-style: cursos em PROSA, itens a)/b)/c) — "curso de graduacao em
        # Medicina, cod. 1202539, da Universidade Federal do Para (cod. 569)"
        rx_prosa = re.compile(
            r"c[oó]d\.?\s*(\d{4,8})\s*,?\s*d[aeo]s?\s+([^,(;]{4,90}?)\s*"
            r"\(\s*c[oó]d\.?\s*(\d{2,7})\s*\)", re.I)
        vistos = set()
        for mm in rx_prosa.finditer(texto):
            if mm.group(1) in vistos:      # itens a)/b) repetem padrao: dedup por cod
                continue
            vistos.add(mm.group(1))
            cursos.append({"cod_curso": mm.group(1), "cod_ies": mm.group(3),
                           "ies": mm.group(2).strip()})
    return numero, medidas, cursos, sem_codigo


def main(links):
    if os.path.exists(DESTINO):
        atual = json.load(open(DESTINO, encoding="utf-8"))
    else:
        atual = {"fonte": "", "portarias": {}}
    if not links:                      # sem argumento: re-valida o que ja esta la
        links = [p["url"] for p in atual["portarias"].values()]
        print(f"[cautelares] sem links novos — revalidando {len(links)} do JSON atual")

    novas = dict(atual["portarias"])
    for url in links:
        numero, medidas, cursos, sem_cod = extrair_portaria(url)
        if not numero or not cursos:
            print(f"[cautelares] {url}: numero={numero!r} cursos={len(cursos)} — "
                  f"NAO gravado (conferir a mao)")
            continue
        aviso = f" | {sem_cod} linha(s) SEM codigo ficaram fora" if sem_cod else ""
        print(f"[cautelares] Portaria {numero}: {len(cursos)} cursos, "
              f"medidas={medidas}{aviso}")
        novas[numero] = {"url": url, "medidas": medidas, "cursos": cursos}

    atual["portarias"] = dict(sorted(novas.items(), key=lambda kv: int(kv[0])))
    atual["fonte"] = ("Portarias SERES/MEC (cautelares do Enamed) lidas do DOU por "
                      "atualizar_cautelares.py — deterministico, sem IA")
    if os.path.exists(DESTINO):
        os.replace(DESTINO, DESTINO + ".bak")
    with open(DESTINO, "w", encoding="utf-8") as fh:
        json.dump(atual, fh, ensure_ascii=False, indent=1)
    tot = sum(len(v["cursos"]) for v in atual["portarias"].values())
    print(f"[ok] {DESTINO}: {len(atual['portarias'])} portarias, {tot} cursos "
          f"(anterior salvo em .bak)")


if __name__ == "__main__":
    main([a for a in sys.argv[1:] if a.startswith("http")])
