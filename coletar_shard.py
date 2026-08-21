"""Um robo da coleta dividida: busca no Google News APENAS a sua fatia das keywords.

Roda em paralelo com os outros no GitHub Actions. Cada job sai de um IP diferente
(medido: 20.119.x, 20.168.x, 135.232.x, 172.182.x numa mesma execucao), entao cada robo
mantem o mesmo ritmo seguro de 0.5s entre buscas SEM que o conjunto vire uma rajada do
mesmo IP — que foi o que derrubou a coleta de ~225 para 12 noticias quando se tentou
concorrencia dentro de um processo so.

Ganho medido com 4 robos: 153s -> ~28s, com cobertura identica (3549 itens, 13 vazias).

Grava a fatia em gn_shard_<N>.csv, que o job final junta (ver juntar_shards.py).
"""
import os
import sys

import clipping_core as cc

SHARD = int(os.environ.get("SHARD", "1"))
SHARDS = int(os.environ.get("SHARDS", "4"))
WHEN = os.environ.get("WHEN", "1d") or "1d"


def main():
    cc.set_vertical(os.environ.get("VERTICAL", "saude_educacao"))
    kws = cc.fatia_keywords(SHARD, SHARDS)
    if not kws:
        print(f"[shard {SHARD}] nenhuma keyword nesta fatia", flush=True)
        sys.exit(1)

    print(f"[shard {SHARD}/{SHARDS}] {len(kws)} keywords | when={WHEN}", flush=True)
    # o Brazil Stock Guide e uma busca so, nao fatiada: roda no robo 1 para nao duplicar
    df, n_bsg = cc._google_news(WHEN, kws=kws, incluir_bsg=(SHARD == 1))

    saida = f"gn_shard_{SHARD}.csv"
    df.to_csv(saida, index=False, encoding="utf-8")
    print(f"[shard {SHARD}] {len(df)} itens (BSG={n_bsg}) -> {saida}", flush=True)

    # Falha o job se a fatia voltou completamente vazia: e sinal de bloqueio, e e melhor
    # o robo aparecer VERMELHO no Actions do que o clipping sair silenciosamente incompleto.
    if df.empty:
        print(f"[shard {SHARD}] ERRO: fatia vazia — provavel bloqueio do Google", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
