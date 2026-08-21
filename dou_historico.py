"""Levantamento historico dos atos que AUTORIZAM ou BARRAM cursos superiores (2018 -> hoje).

POR QUE PELA EDICAO DIARIA, E NAO PELA BUSCA POR FRASE
------------------------------------------------------
A busca do DOU (`/consulta/-/buscar/dou?q="termo"`) depende de acertar a frase, e portaria
que usa outra redacao escapa em silencio — justamente o tipo de perda invisivel que nao
queremos num levantamento historico. Ja a EDICAO DIARIA devolve TODOS os atos publicados no
dia, cada um com o campo `hierarchyStr` dizendo o orgao que assinou. Filtrando por
"Ministerio da Educacao" pegamos o universo completo, independente de redacao.

Medido: 03/12/2018 -> 393 atos no dia, 17 do MEC, 4 da SERES.
        20/08/2026 -> 346 atos no dia, 11 do MEC, 1 da SERES.

A busca por frase ainda tem uso: e a rede de seguranca para atos de FORA do MEC (CNE, CD,
gabinete do ministro) que tratem de curso. Ver `dou_historico_extra.py` se um dia precisar.

MECANICA (tudo medido, nao suposto)
-----------------------------------
- edicao do dia:  https://www.in.gov.br/leiturajornal?data=DD-MM-AAAA&secao=do1
- o conteudo vem num <script> cujo JSON comeca com {"typeNormDay" — NAO e o mesmo bloco
  <script type="application/json"> da pagina de busca; aquele vem vazio aqui.
- campos por ato: pubName, urlTitle, numberPage, title, pubDate, content, editionNumber,
  artType, hierarchyStr, hierarchyList, pubOrder
- texto integral: https://www.in.gov.br/web/dou/-/<urlTitle>  (div class="texto-dou")
- o WAF do in.gov.br DERRUBA a conexao se o User-Agent nao parecer de navegador.
- o endpoint FALHA DE FORMA INTERMITENTE (devolve a pagina sem o bloco JSON). Por isso todo
  fetch aqui tem retry — sem ele, dias inteiros sumiriam do levantamento sem aviso.

Uso:
    python dou_historico.py 2018 2026          # coleta o periodo
    python dou_historico.py 2018 2026 --secao do1e
"""
import io
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests

HEADERS = {
    # UA de navegador e OBRIGATORIO: com UA identificavel o WAF derruba a conexao.
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
BASE_DIA = "https://www.in.gov.br/leiturajornal?data={d}&secao={s}"
BASE_ATO = "https://www.in.gov.br/web/dou/-/"
ORGAO_ALVO = "Ministério da Educação"

_RX_BLOCO = re.compile(r'<script[^>]*>(.*?)</script>', re.S)
_RX_TEXTO = re.compile(r'class="texto-dou"(.*?)</div>', re.S)
_RX_TAG = re.compile(r"<[^>]+>")


def _limpa(html):
    return re.sub(r"\s+", " ", _RX_TAG.sub(" ", html or "")).strip()


def _get(url, tentativas=4):
    """GET com retry. O in.gov.br devolve 200 com pagina incompleta de vez em quando —
    quem chama valida o conteudo e pede retry devolvendo None."""
    for i in range(tentativas):
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        time.sleep(1.5 * (i + 1) + random.random())
    return None


def atos_do_dia(dia, secao="do1", tentativas=4):
    """Todos os atos publicados no dia. Devolve None se nem apos os retries veio o JSON —
    None e DIFERENTE de lista vazia: vazio e feriado, None e falha que precisa aparecer."""
    url = BASE_DIA.format(d=dia.strftime("%d-%m-%Y"), s=secao)
    for _ in range(tentativas):
        html = _get(url, tentativas=2)
        if html:
            for bloco in _RX_BLOCO.findall(html):
                b = bloco.strip()
                if b.startswith('{"typeNormDay"'):
                    try:
                        return json.loads(b).get("jsonArray") or []
                    except Exception:
                        break
        time.sleep(2)
    return None


def texto_integral(url_title):
    """Texto completo do ato + o HTML bruto (as tabelas com IES/curso/vagas vivem nele)."""
    html = _get(BASE_ATO + str(url_title).lstrip("/"))
    if not html:
        return "", ""
    m = _RX_TEXTO.search(html)
    return (_limpa(m.group(1)) if m else ""), html


def dias_uteis(ini, fim):
    d = ini
    while d <= fim:
        if d.weekday() < 5:          # DOU nao circula sabado/domingo (edicao extra e a parte)
            yield d
        d += timedelta(days=1)


def coletar(ano_ini, ano_fim, secao="do1", workers=6, log=print):
    ini, fim = date(ano_ini, 1, 1), min(date(ano_fim, 12, 31), date.today())
    dias = list(dias_uteis(ini, fim))
    log(f"[dou] {len(dias)} dias uteis de {ini} a {fim} (secao {secao})")

    achados, falhas, vazios = [], [], 0
    feito = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(atos_do_dia, d, secao): d for d in dias}
        for fut in as_completed(futs):
            d = futs[fut]
            feito += 1
            try:
                arr = fut.result()
            except Exception:
                arr = None
            if arr is None:
                falhas.append(d)
            elif not arr:
                vazios += 1
            else:
                for a in arr:
                    if str(a.get("hierarchyStr", "")).startswith(ORGAO_ALVO):
                        a["_dia"] = d.isoformat()
                        a["_secao"] = secao
                        achados.append(a)
            if feito % 100 == 0:
                log(f"[dou] {feito}/{len(dias)} dias | {len(achados)} atos do MEC | "
                    f"{len(falhas)} falhas")
    log(f"[dou] FIM {secao}: {len(achados)} atos do MEC, {vazios} dias sem edicao, "
        f"{len(falhas)} dias que falharam")
    return achados, falhas


if __name__ == "__main__":
    a1 = int(sys.argv[1]) if len(sys.argv) > 1 else 2018
    a2 = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
    secao = sys.argv[sys.argv.index("--secao") + 1] if "--secao" in sys.argv else "do1"
    atos, falhas = coletar(a1, a2, secao=secao)
    saida = f"dou_mec_{secao}_{a1}_{a2}.json"
    with io.open(saida, "w", encoding="utf-8") as fh:
        json.dump({"atos": atos, "dias_que_falharam": [d.isoformat() for d in falhas]},
                  fh, ensure_ascii=False)
    print(f"[ok] {len(atos)} atos -> {saida}")
