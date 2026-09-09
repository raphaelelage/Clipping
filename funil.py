"""Funil de regulacao: deriva de Atos (log de eventos do DOU) o ESTADO ATUAL de cada curso.

Modelo (ver ARCHITECTURE.md):
  - trilho do curso (fases): 0 protocolado/sobrestado (so Medicina, via SERES) ->
    1 autorizado -> 2 reconhecido -> 3 renovacao de reconhecimento (ciclo) -> F desativado
  - eventos transversais NAO mudam a fase: vagas (aditamento/reducao), medida cautelar,
    sancionador/supervisao, sobrestamento — viram colunas/flags.
  - fase atual = ato do TRILHO com data mais recente (empate: fase maior). Universidades/
    centros universitarios criam curso sem autorizacao (autonomia): o primeiro ato pode
    ser direto o reconhecimento — por isso a fase e "a mais recente", nao "a sequencia".

Fonte da verdade e o log Atos: este modulo NUNCA edita Atos, so (re)escreve a aba Funil.
Uso: python funil.py <arquivo.xlsx>   (ou funil.gerar(caminho) pelo robo)
"""
import sys
import unicodedata

import pandas as pd

# tipo_decisao -> (fase_num, rotulo). Trilho principal do CURSO.
FASE_TRILHO = {
    "autorizacao": (1, "1. Autorizado"),
    "reconhecimento": (2, "2. Reconhecido"),
    "renovacao_reconhecimento": (3, "3. Renovacao de reconhecimento"),
    "desativacao": (9, "F. Desativado"),
}
FASE_PENDENTE = {
    "pendente: em tramitacao": (0, "0. Protocolado (em tramitacao)"),
    "pendente: sobrestado (MC ADC 81)": (0, "0. Sobrestado (ADC 81)"),
}
# transversais viram colunas; atos de IES (credenciamento etc.) nao tem curso e ficam fora
TRANSVERSAIS = {"aditamento_aumento_vagas", "reducao_vagas", "medida_cautelar",
                "sancionador_supervisao", "sobrestamento"}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _limpa(s):
    v = str(s).strip()
    return "" if v.lower() in ("nan", "none", "<na>", "-", "–") else v


# preenchimentos do levantamento que significam "sem referencia judicial de verdade"
_SEM_REF = {"NAO CONSTA NA FONTE", "NAO SE APLICA", ""}


def _tem_ref_judicial(serie):
    return serie.map(lambda v: _norm(v) not in _SEM_REF).any()


def gerar(caminho, log=print):
    xl = pd.ExcelFile(caminho)
    atos = xl.parse("Atos")
    for c in atos.columns:
        if atos[c].dtype == object or str(atos[c].dtype) in ("str", "string"):
            atos[c] = atos[c].map(_limpa)
    atos["_data"] = pd.to_datetime(atos["data_decisao"], errors="coerce")
    atos.loc[atos["_data"].isna(), "_data"] = pd.to_datetime(
        atos.loc[atos["_data"].isna(), "data_pedido"], errors="coerce")

    # chave do curso: cod_curso oficial; sem codigo (linhas novas do radar) cai em
    # IES+curso+municipio normalizados — melhor aproximacao disponivel
    atos["_chave"] = atos["cod_curso"].map(_limpa)
    vazio = atos["_chave"] == ""
    atos.loc[vazio, "_chave"] = ("S/COD|" + atos.loc[vazio, "ies"].map(_norm) + "|"
                                 + atos.loc[vazio, "curso"].map(_norm) + "|"
                                 + atos.loc[vazio, "municipio"].map(_norm))
    atos = atos[atos["curso"] != ""]          # ato so de IES nao entra no funil de cursos

    linhas = []
    for chave, g in atos.groupby("_chave", sort=False):
        g = g.sort_values("_data")
        ult = g.iloc[-1]

        trilho = g[g["tipo_decisao"].isin(FASE_TRILHO)]
        pend = g[g["tipo_decisao"].isin(FASE_PENDENTE)]
        if len(trilho):
            # mais recente; empate na data -> fase maior
            t = trilho.assign(_f=[FASE_TRILHO[x][0] for x in trilho["tipo_decisao"]])
            top = t.sort_values(["_data", "_f"]).iloc[-1]
            fase = FASE_TRILHO[top["tipo_decisao"]][1]
            data_fase, ato_fase = top["_data"], top["ato"]
        elif len(pend):
            top = pend.iloc[-1]
            fase = FASE_PENDENTE[top["tipo_decisao"]][1]
            data_fase, ato_fase = top["_data"], top["ato"]
        else:
            fase, data_fase, ato_fase = ("(sem ato do trilho no periodo)",
                                         ult["_data"], ult["ato"])

        vagas = g[g["numero_vagas"].map(lambda v: _limpa(v) not in ("", "0"))]
        cautelar = g[g["tipo_decisao"] == "medida_cautelar"]
        sanc = g[g["tipo_decisao"] == "sancionador_supervisao"]
        import re as _re
        curso_nome = _norm(ult["curso"])
        # \bMEDICINA\b: sem borda de palavra, BIOMEDICINA conta como Medicina (ja mordeu)
        eh_med = bool(_re.search(r"\bMEDICINA\b", curso_nome)) and "VETERIN" not in curso_nome
        linhas.append({
            "cod_curso": "" if chave.startswith("S/COD|") else chave,
            "curso": ult["curso"], "ies": ult["ies"], "cod_ies": ult["cod_ies"],
            "uf": ult["uf"], "municipio": ult["municipio"],
            "mantenedora": ult["mantenedora"],
            "medicina": "Sim" if eh_med else "",
            "fase_atual": fase,
            "data_fase": data_fase.date() if pd.notna(data_fase) else None,
            "ato_da_fase": ato_fase,
            "vagas_vigentes": (vagas.iloc[-1]["numero_vagas"] if len(vagas) else ""),
            "cautelar": (cautelar.iloc[-1]["_data"].strftime("%d/%m/%Y")
                         if len(cautelar) and pd.notna(cautelar.iloc[-1]["_data"]) else ""),
            "sancionador": (sanc.iloc[-1]["_data"].strftime("%d/%m/%Y")
                            if len(sanc) and pd.notna(sanc.iloc[-1]["_data"]) else ""),
            "via_judicial": "Sim" if _tem_ref_judicial(g["ref_judicial"]) else "",
            "qtd_atos": len(g),
            "processo_recente": ult["processo"],
        })

    funil = pd.DataFrame(linhas).sort_values(
        ["medicina", "fase_atual", "data_fase"], ascending=[False, True, False])

    # reescreve SO a aba Funil, preservando as demais
    abas = {n: xl.parse(n) for n in xl.sheet_names if n != "Funil"}
    with pd.ExcelWriter(caminho, engine="openpyxl",
                        date_format="DD/MM/YYYY", datetime_format="DD/MM/YYYY") as xw:
        for n, df in abas.items():
            df.to_excel(xw, sheet_name=n, index=False)
        funil.to_excel(xw, sheet_name="Funil", index=False)
        for aba in list(abas) + ["Funil"]:
            ws = xw.book[aba]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
    log(f"[funil] {len(funil)} cursos | " + " | ".join(
        f"{k}={v}" for k, v in funil["fase_atual"].value_counts().items()))
    return funil


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else "Regulacao_Cursos.xlsx")
