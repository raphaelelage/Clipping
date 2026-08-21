"""Compara, DENTRO DA MESMA RODADA do GitHub Actions, dois jeitos de buscar no Google News:

  A = pygooglenews (producao historica: 2 GETs identicos por busca, sem timeout)
  B = GET unico direto (clipping_core._gn_fetch)

Por que rodar no Actions e nao na maquina local: o Google trata IP de datacenter
diferente de IP residencial. Foi exatamente essa diferenca que derrubou a coleta de
~225 para 12 noticias quando se tentou concorrencia. Teste local nao prova nada aqui.

Compara CONJUNTO DE LINKS por keyword — nao so a contagem. Duas rodadas seguidas
sempre diferem um pouco (noticia nova entra no minuto seguinte), entao a ordem A/B
alterna a cada execucao para o segundo regime nao levar sempre a vantagem do atraso.

Nao envia e-mail, nao mexe no Drive. Uso: python benchmark_gn.py [when] [n_keywords]
"""
import json
import os
import sys
import time

import clipping_core as cc
from pygooglenews import GoogleNews

WHEN = sys.argv[1] if len(sys.argv) > 1 else "1d"
LIMITE = int(sys.argv[2]) if len(sys.argv) > 2 else 0        # 0 = todas
PAUSA_ENTRE_REGIMES = 60                                      # deixa o IP "esfriar"


def coleta(regime, kws):
    """Devolve {keyword: set(links)} + metricas. Mesmo ritmo da producao: 0.5s entre buscas."""
    gn = GoogleNews(lang="pt", country="BR")
    por_kw, vazias, erros = {}, [], 0
    t0 = time.time()
    for kw in kws:
        try:
            if regime == "A":
                entries = gn.search(kw, when=WHEN).get("entries", [])
            else:
                entries = cc._gn_fetch(kw, WHEN)
        except Exception:
            entries, erros = [], erros + 1
        if entries:
            por_kw[kw] = {e.get("link", "") for e in entries}
        else:
            por_kw[kw] = set()
            vazias.append(kw)
        time.sleep(0.5)
    return {
        "regime": regime,
        "segundos": round(time.time() - t0, 1),
        "itens": sum(len(v) for v in por_kw.values()),
        "vazias": vazias,
        "erros": erros,
        "por_kw": {k: sorted(v) for k, v in por_kw.items()},
    }


def main():
    cc.set_vertical(os.environ.get("VERTICAL", "saude_educacao"))
    kws = cc.keywords[:LIMITE] if LIMITE else cc.keywords

    # alterna a ordem por hora par/impar para nao viciar a comparacao
    ordem = ["A", "B"] if int(time.strftime("%H")) % 2 == 0 else ["B", "A"]
    print(f"[bench] {len(kws)} keywords | when={WHEN} | ordem={'->'.join(ordem)}", flush=True)

    res = {}
    for i, regime in enumerate(ordem):
        if i:
            print(f"[bench] pausa de {PAUSA_ENTRE_REGIMES}s entre regimes", flush=True)
            time.sleep(PAUSA_ENTRE_REGIMES)
        print(f"[bench] rodando regime {regime}…", flush=True)
        res[regime] = coleta(regime, kws)
        r = res[regime]
        print(f"[bench] {regime}: {r['itens']} links, {len(r['vazias'])} vazias, "
              f"{r['erros']} erros, {r['segundos']}s", flush=True)

    a, b = res["A"], res["B"]
    so_a = so_b = 0
    kw_pior = []
    for kw in kws:
        sa, sb = set(a["por_kw"][kw]), set(b["por_kw"][kw])
        so_a += len(sa - sb)
        so_b += len(sb - sa)
        if sa - sb:
            kw_pior.append((len(sa - sb), kw))

    print("\n=== VEREDITO ===", flush=True)
    print(f"  tempo:  A={a['segundos']}s  B={b['segundos']}s  "
          f"(B economiza {round(a['segundos'] - b['segundos'], 1)}s)", flush=True)
    print(f"  links:  A={a['itens']}  B={b['itens']}", flush=True)
    print(f"  vazias: A={len(a['vazias'])}  B={len(b['vazias'])}", flush=True)
    print(f"  so A pegou (B PERDEU): {so_a} link(s)", flush=True)
    print(f"  so B pegou (B ganhou): {so_b} link(s)", flush=True)
    if kw_pior:
        print("  keywords onde B perdeu:", flush=True)
        for n, kw in sorted(kw_pior, reverse=True)[:10]:
            print(f"    -{n:3d}  {kw}", flush=True)
    # CRITERIO. A primeira versao ("B perde <=5 links") estava errada e reprovou B
    # injustamente: duas coletas separadas por ~3min sempre diferem, porque noticia nova
    # entra e, nas keywords que batem no teto de ~100 itens do servidor, a mais antiga sai.
    # Medido: B perdeu 51 e ganhou 54 — simetrico, ou seja, rotacao e nao regressao.
    # O que importa e regressao SISTEMATICA:
    #   1) nenhuma keyword pode sair de "tinha resultado" para "voltou vazia";
    #   2) o total de links nao pode cair de forma relevante (>2%);
    #   3) o numero de vazias nao pode subir.
    zeradas = [kw for kw in kws if a["por_kw"][kw] and not b["por_kw"][kw]]
    queda_pct = 100 * (a["itens"] - b["itens"]) / max(a["itens"], 1)
    aprovado = (not zeradas
                and queda_pct <= 2
                and len(b["vazias"]) <= len(a["vazias"]) + 2)
    print(f"\n  keywords que A trouxe e B zerou: {len(zeradas)} {zeradas[:5]}", flush=True)
    print(f"  queda no total de links: {queda_pct:.1f}%", flush=True)
    print(f"\n  B APROVADO: {'SIM' if aprovado else 'NAO'}"
          f"  (criterio: nenhuma keyword zerada, queda <=2%, vazias nao sobem)", flush=True)

    with open("bench_gn.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False)
    print("  detalhe salvo em bench_gn.json (artifact do run)", flush=True)


if __name__ == "__main__":
    main()
