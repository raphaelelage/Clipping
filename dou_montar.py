"""Monta o Excel final do levantamento historico a partir das linhas extraidas.

QUATRO abas (o usuario pediu poucas):
  1. Atos           — 1 linha por curso afetado por ato, 2018 -> hoje, tudo.
  2. Medicina       — o recorte de cursos de Medicina (vagas, IES, municipio,
                      judicializacao), mesmas colunas.
  3. Medicina_SERES — as duas planilhas OFICIAIS da SERES (processos em tramitacao +
                      sobrestados MC ADC 81, fotografia de 04/06/2024), com os codigos
                      que o DOU nao traz (mantenedora, IES, curso, SEI, regiao de saude).
  4. Notas          — metodologia, dicionario e cobertura, para o arquivo se explicar
                      sozinho daqui a um ano.

DEDUP (regra do levantamento): o mesmo curso aparecendo em atos DIFERENTES ao longo dos
anos e historia, nao duplicata. Duplicata e a mesma combinacao (ato, processo, curso, IES)
— acontece quando a portaria e republicada/retificada — e ai fica a publicacao mais
recente, com a coluna 'retificacao' marcando que houve (*).
"""
import io
import json
import sys

import pandas as pd

COLUNAS = [
    "data_publicacao", "tipo_ato", "ato", "orgao_resumido", "curso", "vagas_num", "vagas",
    "ies", "mantenedora", "municipio", "uf", "processo_emec", "cod_ies", "ref_judicial",
    "retificacao", "endereco", "secao", "pagina", "edicao", "link", "fonte_detalhe",
    "resumo_texto", "orgao",
]

NOTAS = [
    ["O que e este arquivo",
     "Todos os atos do Ministerio da Educacao publicados na Secao 1 do Diario Oficial da "
     "Uniao (incl. edicoes extras nao; ver Limitacoes) de 01/01/2018 ate a data de corte, "
     "que tratam de regulacao de cursos e instituicoes: autorizacao, aumento/reducao de "
     "vagas, reconhecimento, renovacao, credenciamento, descredenciamento, medidas "
     "cautelares, atos sancionadores e sobrestamentos. Uma linha por CURSO afetado."],
    ["Fonte",
     "Edicao diaria do DOU (in.gov.br/leiturajornal), dia a dia util, filtrando os atos "
     "cujo orgao (hierarchyStr) comeca com 'Ministerio da Educacao'. Esse caminho pega o "
     "universo completo do MEC sem depender de acertar palavras de busca."],
    ["Como os campos foram obtidos",
     "Do proprio ato no DOU: a maioria das portarias lista os cursos em tabela (Registro "
     "e-MEC, curso, vagas, mantida, mantenedora, endereco); municipio/UF vem do endereco "
     "de funcionamento. Atos sem tabela (cautelares, sancionadores) viram 1 linha com o "
     "resumo do texto e o processo/codigo e-MEC extraidos do paragrafo."],
    ["tipo_ato",
     "Classificado pelo titulo + inicio do texto: autorizacao, aditamento_aumento_vagas, "
     "reducao_vagas, reconhecimento, renovacao_reconhecimento, credenciamento, "
     "recredenciamento, descredenciamento, medida_cautelar, sancionador_supervisao, "
     "sobrestamento, desativacao, chamamento_mais_medicos, certificacao_cebas (filantropia,"
     " mantida a parte), outro."],
    ["ref_judicial",
     "Preenchido quando o texto do ato menciona mandado de seguranca, decisao judicial, "
     "liminar, tutela ou a ADC 81 do STF — e o marcador dos atos judicializados."],
    ["Dedup",
     "O mesmo curso em atos diferentes ao longo dos anos e a historia regulatoria do curso "
     "(autorizacao -> reconhecimento -> renovacao), nao duplicata. Duplicata removida: "
     "mesma combinacao (ato, processo, curso, IES), ficando a publicacao mais recente."],
    ["Aba Medicina_SERES",
     "Planilhas oficiais da SERES (fotografia de 04/06/2024): processos de Medicina em "
     "tramitacao (196 judiciais + 98 administrativos) e 93 sobrestados pela Medida "
     "Cautelar da ADC 81/STF. Traz codigos de mantenedora/IES/curso, n. SEI, n. do "
     "processo judicial e regiao de saude. 'consta_no_dou_coletado' cruza o processo "
     "e-MEC com a aba Atos — sobrestado/tramitando por definicao NAO tem decisao "
     "publicada, entao a maioria nao consta mesmo."],
    ["Limitacoes",
     "1) Secao DO1 apenas; edicoes extras (DO1E) sao raras para o MEC mas existem. "
     "2) ~12% das linhas de tabela vem sem municipio (enderecos fora do padrao). "
     "3) Atos de 'texto corrido' dependem de regex — conferir o link quando for decisivo. "
     "4) Dias em que o DOU nao circulou (feriados) nao tem atos mesmo."],
]


def montar(parquet, saida, oficial_parquet=None, log=print):
    df = pd.read_parquet(parquet)
    log(f"[montar] {len(df)} linhas brutas")

    # tipos e ordenacao
    df["data"] = pd.to_datetime(df["data_publicacao"], format="%d/%m/%Y", errors="coerce")
    df["orgao_resumido"] = df["orgao"].str.split("/").str[-1].str.strip()
    df = df.sort_values("data")

    # dedup de republicacao: mesma (ato, processo, curso, ies) -> fica a mais recente
    chave = ["ato", "processo_emec", "curso", "ies"]
    for c in chave:
        if c not in df:
            df[c] = ""
    antes = len(df)
    df = df.drop_duplicates(subset=chave, keep="last")
    log(f"[montar] dedup de republicacao: -{antes - len(df)} linhas")

    for c in COLUNAS:
        if c not in df:
            df[c] = ""
    corpo = df[COLUNAS].copy()

    med = corpo[corpo["curso"].str.contains("MEDICINA", case=False, na=False)
                & ~corpo["curso"].str.contains("VETERIN", case=False, na=False)]
    log(f"[montar] Atos={len(corpo)} | Medicina={len(med)}")

    oficial = pd.read_parquet(oficial_parquet) if oficial_parquet else None
    if oficial is not None:
        # CONTRAPROVA: processo oficial cuja decisao ja saiu deve existir no DOU coletado
        emec_dou = set(corpo["processo_emec"].dropna().astype(str))
        oficial["consta_no_dou_coletado"] = oficial["ref_emec"].astype(str).isin(emec_dou)
        log(f"[contraprova] {oficial['consta_no_dou_coletado'].sum()}/{len(oficial)} processos "
            f"oficiais tem ato no DOU coletado (esperado: baixo — tramitando/sobrestado = "
            f"decisao NAO publicada; os que constam sao aditamentos/atos intermediarios)")

    notas = pd.DataFrame(NOTAS, columns=["Assunto", "Descricao"])
    with pd.ExcelWriter(saida, engine="openpyxl") as xw:
        corpo.to_excel(xw, sheet_name="Atos", index=False)
        med.to_excel(xw, sheet_name="Medicina", index=False)
        if oficial is not None:
            oficial.to_excel(xw, sheet_name="Medicina_SERES", index=False)
        notas.to_excel(xw, sheet_name="Notas", index=False)
        for aba in (("Atos", "Medicina", "Medicina_SERES") if oficial is not None
                    else ("Atos", "Medicina")):
            ws = xw.book[aba]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
    log(f"[ok] {saida}")
    return corpo, med


if __name__ == "__main__":
    parquet = sys.argv[1]
    saida = sys.argv[2] if len(sys.argv) > 2 else "atos_regulacao_2018_2026.xlsx"
    oficial = sys.argv[3] if len(sys.argv) > 3 else None
    montar(parquet, saida, oficial)
