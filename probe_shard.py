"""Prova de conceito da coleta dividida em varios robos simultaneos.

Cada job do Actions roda este script com um SHARD diferente e coleta APENAS a sua fatia
das keywords, no mesmo ritmo da producao (0.5s entre buscas). Os quatro rodam ao mesmo
tempo, cada um do seu proprio IP.

A pergunta que isto responde: quatro robos simultaneos sofrem throttle do Google?
Se cada fatia voltar com contagem normal e poucas vazias, dividir e seguro e o tempo do
Google News cai por um fator de ~4 (a maior parte dele e pausa deliberada, nao rede).
Se as vazias explodirem, o Google limita por FAIXA de IP e a ideia morre aqui — barato.
"""
import os
import time

import requests

import clipping_core as cc

SHARD = int(os.environ.get("SHARD", "1"))
SHARDS = int(os.environ.get("SHARDS", "4"))


def main():
    cc.set_vertical(os.environ.get("VERTICAL", "saude_educacao"))
    # fatia intercalada (1 de cada 4) em vez de blocos contiguos: assim toda fatia recebe
    # uma mistura de keywords produtivas e raras, e as fatias ficam comparaveis entre si.
    kws = cc.keywords[SHARD - 1::SHARDS]

    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception:
        ip = "?"
    print(f"[shard {SHARD}/{SHARDS}] ip={ip} keywords={len(kws)}", flush=True)

    t0 = time.time()
    itens, vazias = 0, []
    for kw in kws:
        try:
            e = cc._gn_fetch(kw, "1d")
        except Exception:
            e = []
        if e:
            itens += len(e)
        else:
            vazias.append(kw)
        time.sleep(0.5)
    dt = round(time.time() - t0, 1)

    print(f"[shard {SHARD}] RESULTADO ip={ip} itens={itens} "
          f"vazias={len(vazias)}/{len(kws)} segundos={dt}", flush=True)
    if vazias:
        print(f"[shard {SHARD}] vazias: {', '.join(vazias[:12])}", flush=True)


if __name__ == "__main__":
    main()
