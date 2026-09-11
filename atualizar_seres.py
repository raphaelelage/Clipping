"""Troca a FOTO da planilha SERES (pendentes de Medicina) na base — deterministico.

A parte arriscada (PDF -> tabela) fica para a sessao assistida descrita em
PROMPT_ATUALIZAR_SERES.md; ESTE script faz só a cirurgia segura no Excel:

    python atualizar_seres.py --arquivo Regulacao_Cursos.xlsx --data 05/08/2027 \
           --tramitacao tramitacao.csv --sobrestados sobrestados.csv

O que ele faz:
  1. reconstroi a aba Medicina_SERES = tramitacao + sobrestados (coluna 'lista' diz
     de qual PDF veio; demais colunas copiadas como vierem);
  2. REMOVE da aba Atos todas as linhas da foto antiga (tipo_decisao 'pendente: ...')
     e INSERE as novas com o contrato EXATO que o funil entende (tipos
     'pendente: em tramitacao' / 'pendente: sobrestado (MC ADC 81)' — strings que
     casam com funil.FASE_PENDENTE, NAO mudar);
  3. imprime antes/depois para conferencia. Depois: regenerar o funil (o robo faz na
     proxima rodada, ou rode python funil.py <arquivo>).

Colunas MINIMAS nos CSVs: ref_emec (processo e-MEC) e ies. Reconhecidas quando
existirem: data_protocolo, natureza, tipo_processo, regime_juridico, ref_sei,
ref_judicial, cod_mantenedora, mantenedora, cod_ies, municipio, uf, regiao_saude,
situacao_mec, cod_curso, curso. Nada e inventado: coluna ausente fica vazia.
"""
import os
import sys

import pandas as pd

LINK_SERES = ("https://www.gov.br/mec/pt-br/assuntos/es/cursos-de-medicina/"
              "regulacao-e-supervisao/documentos")
TIPO_TRAM = "pendente: em tramitacao"
TIPO_SOBR = "pendente: sobrestado (MC ADC 81)"


def _ler(caminho):
    if caminho.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(caminho, dtype=str)
    else:
        df = pd.read_csv(caminho, dtype=str, sep=None, engine="python")
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "ref_emec" not in df.columns and "processo" in df.columns:
        df = df.rename(columns={"processo": "ref_emec"})
    faltando = {"ref_emec", "ies"} - set(df.columns)
    if faltando:
        sys.exit(f"{caminho}: faltam colunas obrigatorias {sorted(faltando)}")
    return df.fillna("")


def _linha_ato(r, tipo, data_foto):
    def g(c):
        return str(r.get(c, "") or "").strip()
    return {
        "data_pedido": pd.to_datetime(g("data_protocolo"), dayfirst=True,
                                      errors="coerce"),
        "data_decisao": pd.NaT,
        "tipo_decisao": tipo,
        "ato": "nao consta na fonte",
        "uf": g("uf"), "municipio": g("municipio"),
        "mantenedora": g("mantenedora"), "cod_mantenedora": g("cod_mantenedora"),
        "ies": g("ies"), "cod_ies": g("cod_ies"), "cod_curso": g("cod_curso"),
        "curso": g("curso") or "MEDICINA",
        "numero_vagas": "", "processo": g("ref_emec"),
        "situacao_recurso": "nao se aplica (sem decisao)",
        "ref_judicial": g("ref_judicial"),
        "orgao_resumido": "SERES (planilha oficial)",
        "resumo_texto": ("Natureza: " + (g("natureza") or "?") + "; Tipo: "
                         + (g("tipo_processo") or "?") + "; Regime: "
                         + (g("regime_juridico") or "?"))[:300],
        "retificacao": "Nao",
        "fonte_detalhe": f"planilha oficial SERES ({data_foto})",
        "link": LINK_SERES, "recurso_ref_processo": "",
    }


def main(arquivo, data_foto, tram_csv, sobr_csv):
    tram = _ler(tram_csv) if tram_csv else pd.DataFrame()
    sobr = _ler(sobr_csv) if sobr_csv else pd.DataFrame()
    if not len(tram) and not len(sobr):
        sys.exit("nada a fazer: passe --tramitacao e/ou --sobrestados")

    xl = pd.ExcelFile(arquivo)
    atos = xl.parse("Atos")
    antes = atos["tipo_decisao"].astype(str).str.startswith("pendente")
    print(f"[seres] foto antiga: {int(antes.sum())} pendente(s) na aba Atos — saem")
    atos = atos[~antes]

    novas = []
    for _, r in tram.iterrows():
        novas.append(_linha_ato(r, TIPO_TRAM, data_foto))
    for _, r in sobr.iterrows():
        novas.append(_linha_ato(r, TIPO_SOBR, data_foto))
    df_novas = pd.DataFrame(novas)
    for c in atos.columns:
        if c not in df_novas.columns:
            df_novas[c] = ""
    atos = pd.concat([atos, df_novas.reindex(columns=atos.columns, fill_value="")],
                     ignore_index=True)
    print(f"[seres] foto nova ({data_foto}): {len(tram)} em tramitacao + "
          f"{len(sobr)} sobrestado(s) entram")

    aba = pd.concat([tram.assign(lista="tramitacao"),
                     sobr.assign(lista="sobrestado MC ADC 81")], ignore_index=True)
    aba["fonte"] = f"planilha oficial SERES ({data_foto}) — {LINK_SERES}"

    cu = atos["curso"].astype(str).str.upper()
    med = atos[cu.str.contains(r"\bMEDICINA\b", regex=True, na=False)
               & ~cu.str.contains("VETERIN", na=False)]
    outras = {n: xl.parse(n) for n in xl.sheet_names
              if n not in ("Atos", "Medicina", "Medicina_SERES",
                           "Funil", "Graficos", "Graf_Dados")}
    with pd.ExcelWriter(arquivo, engine="openpyxl", date_format="DD/MM/YYYY",
                        datetime_format="DD/MM/YYYY") as xw:
        atos.to_excel(xw, sheet_name="Atos", index=False)
        med.to_excel(xw, sheet_name="Medicina", index=False)
        aba.to_excel(xw, sheet_name="Medicina_SERES", index=False)
        for n, d in outras.items():
            d.to_excel(xw, sheet_name=n, index=False)
        for w in xw.book.worksheets:
            w.freeze_panes = "A2"
            w.auto_filter.ref = w.dimensions
    print(f"[ok] {arquivo}: Medicina_SERES={len(aba)} linhas | Atos={len(atos)}")
    print("[seres] AGORA: regenerar o funil (python funil.py <arquivo>) e atualizar a "
          "data da foto em funil.ST_SOBRESTADO — ver PROMPT_ATUALIZAR_SERES.md")


if __name__ == "__main__":
    argv = sys.argv[1:]

    def pega(nome, padrao=None):
        return argv[argv.index(f"--{nome}") + 1] if f"--{nome}" in argv else padrao
    arq = pega("arquivo", "Regulacao_Cursos.xlsx")
    data = pega("data")
    if not data:
        sys.exit("--data DD/MM/AAAA (a data da nova foto) e obrigatoria")
    main(arq, data, pega("tramitacao"), pega("sobrestados"))
