# -*- coding: utf-8 -*-
"""A aba "ANS Net Adds" do app do Clipping.

Fica num modulo proprio porque o `streamlit_app.py` ja tem 866 linhas e esta aba
nao compartilha nada com o clipping de noticias — so o jeito de disparar
(workflow_dispatch no GitHub) e de editar arquivo do repo (contents API).

O que ela faz:
  • Rodar agora       -> dispara o workflow (coleta a Sala, grava no BQ, manda)
  • So a tabela       -> mesmo workflow com --so-tabela: nao toca na ANS,
                         remonta o e-mail do historico que ja esta no BigQuery
  • Agendar           -> cron-job.org, mesmo mecanismo do clipping diario
  • Grupos            -> edita blast_grupos.json no repo
  • Colunas           -> 12 meses x 4 escolhas, grava blast_colunas.json

Recebe do app hospedeiro as funcoes que ja existem la (dispatch, leitura/escrita
de arquivo, cron), para nao duplicar token nem tratamento de erro.
"""
from __future__ import annotations

import json

import streamlit as st

import blast_periodos as bp

GRUPOS = "blast_grupos.json"
COLUNAS = "blast_colunas.json"
SECOES = [("medico", "Health plans"), ("odonto", "Dental plans"),
          ("corporate", "Corporate")]


def _carregar(gh_get, caminho, vazio):
    bruto, sha = gh_get(caminho)
    if bruto is None:
        return None, None
    try:
        return json.loads(bruto), sha
    except Exception:                                             # noqa: BLE001
        return vazio, sha


# --------------------------------------------------------------- grupos
def _editor_grupos(gh_get, gh_put):
    cfg, sha = _carregar(gh_get, GRUPOS, {})
    if cfg is None:
        st.warning(f"Não consegui ler `{GRUPOS}` do repositório. "
                   "Não edite agora para não sobrescrever.")
        return

    st.caption("Cada grupo soma os registros ANS listados. É uma cópia da relação "
               "que estava na planilha — não tem vínculo com a Base Consolidada.")
    secao = st.selectbox("Seção", [s for s, _ in SECOES],
                         format_func=lambda s: dict(SECOES)[s], key="bl_sec")
    pg = cfg[secao]["por_grupo"]

    linhas = [{"grupo": g, "registros": ", ".join(r)} for g, r in sorted(pg.items())]
    ed = st.data_editor(linhas, num_rows="dynamic", use_container_width=True,
                        key=f"bl_ed_{secao}",
                        column_config={
                            "grupo": st.column_config.TextColumn("Grupo", width="medium"),
                            "registros": st.column_config.TextColumn(
                                "Registros ANS (vírgula)", width="large")})

    if st.button("💾 Salvar grupos", key=f"bl_sv_{secao}"):
        novo = {}
        for l in ed:
            g = (l.get("grupo") or "").strip()
            if not g:
                continue
            regs = [r.strip().zfill(6) for r in
                    str(l.get("registros") or "").replace(";", ",").split(",")
                    if r.strip()]
            if regs:
                novo[g] = regs
        cfg[secao]["por_grupo"] = novo
        texto = json.dumps(cfg, ensure_ascii=False, indent=1)
        ok = gh_put(GRUPOS, texto, sha, f"grupos do Blast: {secao}")
        st.success("Salvo.") if ok else st.error("Falhou ao salvar.")


# --------------------------------------------------------------- colunas
def _editor_colunas(gh_get, gh_put, ano: int):
    cfg, sha = _carregar(gh_get, COLUNAS, {"meses": {}})
    if cfg is None:
        st.warning(f"Não consegui ler `{COLUNAS}` do repositório.")
        return
    meses = cfg.setdefault("meses", {})

    st.caption("As 4 colunas de Net Adds de cada mês. O padrão já segue a regra "
               "(1º e 2º mês do trimestre mostram o trimestre anterior; o 3º "
               "mostra o que fechou; janeiro a março trocam o YTD pelo ano "
               "anterior; dezembro vira o ano cheio).")

    escolhas = {}
    for m in range(1, 13):
        opts = [o["rotulo"] for o in bp.opcoes(ano, m)]
        atual = meses.get(f"{m:02d}") or [c["rotulo"] for c in bp.padrao(ano, m)]
        st.markdown(f"**{bp.rotulo_mes(ano, m)}**")
        cols = st.columns(4)
        linha = []
        for i, c in enumerate(cols):
            val = atual[i] if i < len(atual) and atual[i] in opts else opts[0]
            linha.append(c.selectbox(f"col {i+1}", opts, index=opts.index(val),
                                     key=f"bl_c_{m}_{i}",
                                     label_visibility="collapsed"))
        escolhas[f"{m:02d}"] = linha

    c1, c2 = st.columns([1, 1])
    if c1.button("💾 Salvar colunas", key="bl_sv_col"):
        cfg["meses"] = escolhas
        ok = gh_put(COLUNAS, json.dumps(cfg, ensure_ascii=False, indent=1), sha,
                    "colunas do Blast")
        st.success("Salvo.") if ok else st.error("Falhou ao salvar.")
    if c2.button("↩️ Voltar ao padrão", key="bl_rs_col"):
        cfg["meses"] = {f"{m:02d}": [c["rotulo"] for c in bp.padrao(ano, m)]
                        for m in range(1, 13)}
        ok = gh_put(COLUNAS, json.dumps(cfg, ensure_ascii=False, indent=1), sha,
                    "colunas do Blast: padrão")
        st.success("Voltou ao padrão.") if ok else st.error("Falhou.")


# --------------------------------------------------------------- seção
def render(*, dispatch, gh_get, gh_put, runs=None, cron_ui=None, ano_padrao: int):
    """A seção inteira do Blast, com as proprias abas.

    E um projeto separado do clipping de noticias — so divide a casca do app
    (token do GitHub, leitura/escrita de arquivo do repo, disparo de workflow).
    Por isso tem as mesmas quatro abas, e nenhuma delas conversa com o clipping.

    `dispatch(modo, destinatarios)` com modo em {'completo', 'tabela'}.
    """
    st.subheader("📊 ANS — Net Adds (Sala de Situação)")
    st.caption("Reconstrói as tabelas do Blast direto da Sala de Situação da ANS "
               "e manda por e-mail, com a planilha das três bases em anexo.")

    t_run, t_cfg, t_sched, t_dbg = st.tabs(
        ["▶️ Rodar agora", "⚙️ Config", "🕗 Agendamento", "🔧 Debug"])

    with t_run:
        to = st.text_input("E-mails", value="raphael.elage.s@gmail.com",
                           key="bl_to", help="Separe por vírgula.")
        c1, c2 = st.columns(2)
        if c1.button("📨 Coletar e enviar", key="bl_run"):
            with st.spinner("disparando…"):
                dispatch("completo", to)
        if c2.button("🔁 Só a tabela", key="bl_tab",
                     help="Não consulta a ANS: remonta o e-mail com o histórico "
                          "que já está no BigQuery. Use depois de mexer em "
                          "grupos ou colunas."):
            with st.spinner("disparando…"):
                dispatch("tabela", to)

    with t_cfg:
        sub_g, sub_c = st.tabs(["👥 Grupos", "🗓️ Colunas"])
        with sub_g:
            _editor_grupos(gh_get, gh_put)
        with sub_c:
            _editor_colunas(gh_get, gh_put, ano_padrao)

    with t_sched:
        if cron_ui:
            cron_ui()
        else:
            st.caption("Agendamento ainda não ligado para o Blast — por enquanto "
                       "o disparo é manual, pela aba Rodar agora.")

    with t_dbg:
        if runs:
            for r in runs():
                st.write(f"{r.get('status')} · {r.get('conclusion') or '—'} · "
                         f"{r.get('created_at', '')[:16].replace('T', ' ')} · "
                         f"[log]({r.get('html_url')})")
        else:
            st.caption("Sem leitura de runs.")
