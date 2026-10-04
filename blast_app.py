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
  • Textos            -> os tres rascunhos de WhatsApp, com as marcas {...}

Recebe do app hospedeiro as funcoes que ja existem la (dispatch, leitura/escrita
de arquivo, cron), para nao duplicar token nem tratamento de erro.
"""
from __future__ import annotations

import datetime as _dt
import json

import streamlit as st

import blast_periodos as bp
import blast_textos as bx

GRUPOS = "blast_grupos.json"
COLUNAS = "blast_colunas.json"
TEXTOS = "blast_textos.json"
# So estas duas tem lista de grupos propria. As secoes "corporate_*" do e-mail
# reaproveitam os grupos da secao base (ver blast_tabela.montar).
SECOES = [("medico", "Health plans"), ("odonto", "Dental plans")]


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
    pg = cfg.setdefault(secao, {}).setdefault("por_grupo", {})

    linhas = [{"grupo": g, "registros": ", ".join(r)} for g, r in sorted(pg.items())]
    ed = st.data_editor(linhas, num_rows="dynamic", width="stretch",
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

    st.caption("As colunas de Net Adds de cada mês. O padrão segue a regra "
               "(1º e 2º mês do trimestre mostram o trimestre anterior; o 3º "
               "mostra o que fechou; janeiro a março trocam o YTD pelo ano "
               "anterior; dezembro vira o ano cheio). Cada mês pode ter uma "
               "quantidade diferente — a tabela se ajusta.")

    escolhas = {}
    for m in range(1, 13):
        atual = [c for c in (meses.get(f"{m:02d}") or bp.padrao_chaves(m))
                 if c in bp.CHAVES]
        sel = st.multiselect(
            bp.MESES[m - 1], bp.CHAVES, default=atual, key=f"bl_c_{m}",
            help="A ordem em que você escolhe é a ordem das colunas. "
                 "São relativas ao mês de referência, então valem todo ano.")
        escolhas[f"{m:02d}"] = sel
        if not sel:
            st.caption("↳ vazio: este mês cai no padrão automático.")

    c1, c2 = st.columns(2)
    if c1.button("💾 Salvar colunas", key="bl_sv_col"):
        cfg["meses"] = {k: v for k, v in escolhas.items() if v}
        ok = gh_put(COLUNAS, json.dumps(cfg, ensure_ascii=False, indent=1), sha,
                    "colunas do Blast")
        st.success("Salvo.") if ok else st.error("Falhou ao salvar.")
    if c2.button("↩️ Voltar ao padrão", key="bl_rs_col"):
        cfg["meses"] = {f"{m:02d}": bp.padrao_chaves(m) for m in range(1, 13)}
        ok = gh_put(COLUNAS, json.dumps(cfg, ensure_ascii=False, indent=1), sha,
                    "colunas do Blast: padrão")
        st.success("Voltou ao padrão.") if ok else st.error("Falhou.")


# --------------------------------------------------------------- textos
_AJUDA = """
**Como marcar onde entra o número** — chave entre `{}`, nesta ordem ou em
qualquer outra:

| marca | o que sai |
|---|---|
| `{SULA net_adds QTD}` | net adds da SULA no trimestre em curso, em milhares |
| `{Market lives}` | vidas do mercado no mês de referência |
| `{HAPV yoy}` | Base Growth YoY, em % |
| `{odonto ODPV net_adds Mês}` | o mesmo, na seção odontológica |
| `{rotulo Mês}` · `{rotulo Trimestre}` | `Aug-26` · `2Q26` — o rótulo, não o número |

**métricas** `lives` · `net_adds` (padrão) · `growth` · `mom` · `yoy` · `rotulo` ·
**períodos** `Mês` (padrão) · `QTD` · `Trimestre` · `YTD` · `Ano` ·
**seções** `medico` (padrão) · `odonto` · `corporate` · `corporate_odonto` ·
**extras** `abs` (vidas em vez de milhares) · `mod` (sem o sinal de menos)

O que não é nenhuma dessas palavras vira o nome do grupo — então `Porto Seguro`
e `Unimed Seguros` funcionam sem aspas. Marca que não resolve **não desaparece**:
sai como «assim» no e-mail, para você ver que faltou número.
"""


def _editor_textos(gh_get, gh_put, ano: int, previa=None):
    cfg, sha = _carregar(gh_get, TEXTOS, {})
    if cfg is None:
        st.warning(f"Não consegui ler `{TEXTOS}` do repositório. "
                   "Não edite agora para não sobrescrever.")
        return
    mods = {k: ((cfg.get("modelos") or {}).get(k) or bx.MODELOS_PADRAO[k])
            for k in ("1", "2", "3")}

    st.caption("Um rascunho para cada posição do mês dentro do trimestre. O "
               "e-mail já vem com o do mês certo, preenchido — e o próprio corpo "
               "do e-mail (texto puro) é o rascunho, para copiar do celular.")
    with st.expander("📖 As marcas que o robô substitui"):
        st.markdown(_AJUDA)

    novos = {}
    for k in ("1", "2", "3"):
        atual = bx.escolher(_dt.date.today().month) == k
        st.markdown(f"**{bx.POSICOES[k]}**" + ("  ·  ⬅️ é o deste mês" if atual else ""))
        novos[k] = st.text_area(bx.POSICOES[k], value=mods[k], height=240,
                                key=f"bl_tx_{k}", label_visibility="collapsed")
        problemas = bx.validar(novos[k])
        if problemas:
            st.error("· ".join(problemas[:6]))
        else:
            st.caption(f"✅ {len(bx.marcas(novos[k]))} marcas, todas reconhecidas.")
        with st.expander("Como o robô leu cada marca"):
            st.dataframe(
                [{"marca": m, **{kk: vv for kk, vv in
                                 _leitura(m).items()}} for m in bx.marcas(novos[k])],
                width="stretch", hide_index=True)
        st.divider()

    c1, c2, c3 = st.columns(3)
    if c1.button("💾 Salvar textos", key="bl_sv_tx"):
        if any(bx.validar(v) for v in novos.values()):
            st.error("Tem marca que não reconheço — corrija antes de salvar.")
        else:
            cfg["modelos"] = novos
            ok = gh_put(TEXTOS, json.dumps(cfg, ensure_ascii=False, indent=1), sha,
                        "textos do Blast")
            st.success("Salvo.") if ok else st.error("Falhou ao salvar.")
    if c2.button("↩️ Voltar ao padrão", key="bl_rs_tx"):
        cfg["modelos"] = dict(bx.MODELOS_PADRAO)
        ok = gh_put(TEXTOS, json.dumps(cfg, ensure_ascii=False, indent=1), sha,
                    "textos do Blast: padrão")
        st.success("Voltou ao padrão — recarregue a página.") if ok else st.error("Falhou.")
    k_mes = bx.escolher(_dt.date.today().month)
    c3.download_button("⬇️ Baixar .txt", novos[k_mes],
                       file_name=f"blast_texto_{k_mes}.txt", key="bl_dl_tx",
                       help="O rascunho deste mês, como está aqui (sem preencher).")

    if previa:
        st.markdown("**👁️ Prévia com os números de verdade**")
        st.caption("Lê o histórico no BigQuery e preenche as marcas. Só funciona "
                   "onde há credencial do Google — no seu PC, sim; no Streamlit "
                   "Cloud, não (lá o e-mail é que traz o texto pronto).")
        if st.button("Preencher agora", key="bl_pv_tx"):
            with st.spinner("lendo o histórico…"):
                texto, erro = previa(novos[k_mes])
            if erro:
                st.info(erro)
            else:
                st.code(texto, language="text")
                st.download_button("⬇️ Baixar preenchido", texto,
                                   file_name="blast_texto.txt", key="bl_dl_pv")


def _leitura(marca: str) -> dict:
    """Como o parser entendeu a marca — e aqui que o dono ve que `{SULA QTD}`
    virou net adds, e nao vidas."""
    p = bx._parse(marca)
    return {"seção": p["secao"], "grupo": p["grupo"] or "—",
            "métrica": p["metrica"], "período": p["periodo"],
            "extras": ", ".join(sorted(p["extras"])) or "—"}


# --------------------------------------------------------------- seção
def render(*, dispatch, gh_get, gh_put, runs=None, logs=None, diagnostico=None,
           cron_ui=None, previa_texto=None, ano_padrao: int):
    """A seção inteira do Blast, com as proprias abas.

    E um projeto separado do clipping de noticias — so divide a casca do app
    (token do GitHub, leitura/escrita de arquivo do repo, disparo de workflow).
    Por isso tem as mesmas quatro abas, e nenhuma delas conversa com o clipping.

    `dispatch(modo, destinatarios)` com modo em {'completo', 'tabela'}.
    """
    st.subheader("📊 ANS — Net Adds (Sala de Situação)")
    st.caption("Reconstrói as tabelas direto da Sala de Situação da ANS e manda "
               "por e-mail, com a planilha em anexo. No seu PC roda em duas "
               "fases (grupos, depois todas as operadoras com as quebras); no "
               "GitHub só a primeira, para não gastar cota.")

    t_run, t_cfg, t_txt, t_sched, t_dbg = st.tabs(
        ["▶️ Rodar agora", "⚙️ Config", "📝 Textos", "🕗 Agendamento", "🔧 Debug"])

    with t_run:
        to = st.text_input("E-mails", value="raphael.elage.s@gmail.com",
                           key="bl_to", help="Separe por vírgula.")
        c1, c2, c3 = st.columns(3)
        if c1.button("📨 Coletar e enviar", key="bl_run"):
            with st.spinner("disparando…"):
                dispatch("completo", to)
        if c2.button("🔁 Só a tabela", key="bl_tab",
                     help="Não consulta a ANS: remonta o e-mail com o histórico "
                          "que já está no BigQuery. Use depois de mexer em "
                          "grupos ou colunas."):
            with st.spinner("disparando…"):
                dispatch("tabela", to)
        if c3.button("📦 Base completa", key="bl_f2",
                     help="O segundo e-mail: todas as operadoras e as quebras "
                          "por faixa etária e UF, com a planilha inteira. "
                          "Também remonta do BigQuery — só existe depois que a "
                          "varredura do PC rodou."):
            with st.spinner("disparando…"):
                dispatch("tabela", to, fase="2")
        st.caption("No PC a tarefa **BBI net adds** (08:30) coleta e pede os "
                   "dois e-mails: o dos grupos primeiro, o completo depois. "
                   "Aqui os botões não coletam — só remontam do BigQuery.")

    with t_cfg:
        sub_g, sub_c = st.tabs(["👥 Grupos", "🗓️ Colunas"])
        with sub_g:
            _editor_grupos(gh_get, gh_put)
        with sub_c:
            _editor_colunas(gh_get, gh_put, ano_padrao)

    with t_txt:
        _editor_textos(gh_get, gh_put, ano_padrao, previa=previa_texto)

    with t_sched:
        if cron_ui:
            cron_ui()
        else:
            st.caption("Agendamento ainda não ligado para o Blast — por enquanto "
                       "o disparo é manual, pela aba Rodar agora.")

    with t_dbg:
        # Mesma cara do Debug das outras abas: diagnostico, execucoes com icone e
        # leitura de log dentro do app (debug pelo celular, sem abrir o PC).
        st.markdown("**🩺 Diagnóstico de conexão**")
        if st.button("Checar conexões", key="bl_diag") and diagnostico:
            for rotulo, valor in diagnostico():
                st.write(rotulo, valor)

        st.divider()
        st.markdown("**📊 Últimas execuções**")
        lista = runs() if runs else []
        if not lista:
            st.caption("Nenhuma execução ainda (ou PAT sem acesso a Actions).")
        rotulos = {}
        for r in lista:
            ic = {"success": "✅", "failure": "❌",
                  "cancelled": "⚪"}.get(r.get("conclusion"), "🟡")
            st.markdown(f"{ic} **{r.get('name', 'blast-ans')}** · "
                        f"`{r.get('status')}/{r.get('conclusion')}` · "
                        f"[abrir]({r.get('html_url')}) · "
                        f"{r.get('created_at', '')[:16].replace('T', ' ')}")
            rotulos[f"#{r.get('run_number')} · "
                    f"{r.get('conclusion') or r.get('status')}"] = r.get("id")

        if rotulos and logs:
            st.divider()
            st.markdown("**📜 Ver logs no app** (debug pelo celular, sem abrir o PC)")
            sel = st.selectbox("Execução", list(rotulos.keys()),
                               key="bl_run_log")
            if st.button("Carregar logs", key="bl_logs"):
                st.code(logs(rotulos[sel]) or "(sem log)", language="text")
