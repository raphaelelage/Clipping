"""Avisos de rodada — o UNICO canal para falha engolida.

Regra do projeto: try/except que deixa o clipping seguir NAO pode so imprimir no log.
Chame avisos.aviso("o que falhou e o que ficou faltando"). O clipping.py lista tudo numa
faixa vermelha no topo do e-mail, poe [AVISO] no assunto e escreve no resumo do job do
Actions. Motivo: o radar DOU ficou 5 rodadas morto (2 a 8/set/2026) com "erro nao-fatal"
so no log — e ninguem viu.

Nao e aviso: fonte que legitimamente devolveu 0 itens. E aviso: excecao, HTTP != 200,
API que nao respondeu depois do retry, arquivo do Drive que nao pode ser criado/lido.
"""
LISTA: list = []


def aviso(msg):
    msg = " ".join(str(msg).split())
    if not msg:
        return
    print(f"[AVISO] {msg}", flush=True)
    if msg not in LISTA:
        LISTA.append(msg)


def limpar():
    LISTA.clear()
