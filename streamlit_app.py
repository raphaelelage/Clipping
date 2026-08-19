"""
streamlit_app.py — Painel de controle do Clipping.

NÃO roda a coleta aqui dentro. Ele apenas:
  • dispara o GitHub Action na hora (workflow_dispatch)  -> "Rodar agora"
  • cria/edita agendamentos no cron-job.org (grátis) que disparam o Action no horário
  • edita a configuração da vertical (prompt, palavras-chave, fontes) direto no repositório

Duas verticais independentes: Saúde e Educação (cada uma com seus arquivos e agendamentos).

Secrets (.streamlit/secrets.toml ou painel do Streamlit Cloud):

    github_pat        = "ghp_xxx"      # token com permissão de Actions no repo
    github_owner      = "raphaelelage"
    github_repo       = "Clipping"
    workflow_file     = "clipping.yml"
    branch            = "main"
    cronjob_api_key   = "xxx"          # API key do cron-job.org (opcional: agendamento)
    default_recipients = "voce@exemplo.com"
    app_password      = "..."          # opcional: protege o link público
"""
import base64
import datetime as _dt
import json
import re

import requests
import streamlit as st

st.set_page_config(page_title="Clipping", page_icon="📰", layout="centered")

ACCENT = "#CC092F"
st.markdown(f"""
<style>
  .stButton>button {{ background:{ACCENT};color:#fff;border:0;border-radius:10px;
     padding:.6rem 1rem;font-weight:600;width:100%; }}
  .stButton>button:hover {{ background:#a30724;color:#fff; }}
  .block-container {{ max-width:820px;padding-top:1.5rem; }}
</style>""", unsafe_allow_html=True)

S = st.secrets
PAT = S.get("github_pat", "")
OWNER = S.get("github_owner", "raphaelelage")
REPO = S.get("github_repo", "Clipping")
WF = S.get("workflow_file", "clipping.yml")
BRANCH = S.get("branch", "main")
CRON_KEY = S.get("cronjob_api_key", "")
DEFAULT_TO = S.get("default_recipients", "")
APP_PW = S.get("app_password", "")

GH_API = "https://api.github.com"
CRON_API = "https://api.cron-job.org"
CRON_PREFIX = "Clipping"
DIAS = {"Seg": 1, "Ter": 2, "Qua": 3, "Qui": 4, "Sex": 5, "Sáb": 6, "Dom": 0}

# Verticais — precisa espelhar VERTICAIS/arquivos_vertical do clipping_core.py
VERTICAIS = {
    "saude": {"label": "Saúde", "icon": "🏥",
              "keywords": "keywords_saude.txt", "sources": "sources_saude.txt",
              "prompt": "ai_prompt_saude.txt", "portais": "ANS · Anvisa"},
    "educacao": {"label": "Educação", "icon": "🎓",
                 "keywords": "keywords_educacao.txt", "sources": "sources_educacao.txt",
                 "prompt": "ai_prompt_educacao.txt", "portais": "MEC · Capes"},
}

# ----------------------------------------------------------------- GitHub helpers
def _gh_headers():
    return {"Authorization": f"Bearer {PAT}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}

def dispatch_now(vertical, period, recipients):
    url = f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}/dispatches"
    body = {"ref": BRANCH, "inputs": {"vertical": vertical, "when": period,
                                      "recipients": recipients}}
    return requests.post(url, headers=_gh_headers(), json=body, timeout=30)

def recent_runs(n=8):
    r = requests.get(f"{GH_API}/repos/{OWNER}/{REPO}/actions/runs?per_page={n}",
                     headers=_gh_headers(), timeout=30)
    return r.json().get("workflow_runs", []) if r.status_code == 200 else []

def gh_check():
    return requests.get(f"{GH_API}/repos/{OWNER}/{REPO}", headers=_gh_headers(),
                        timeout=20).status_code

def gh_wf_check():
    return requests.get(f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}",
                        headers=_gh_headers(), timeout=20).status_code

def run_logs_tail(run_id, max_lines=250):
    """Baixa o .zip de logs da execução e devolve as últimas linhas (debug pelo celular)."""
    import io, zipfile
    r = requests.get(f"{GH_API}/repos/{OWNER}/{REPO}/actions/runs/{run_id}/logs",
                     headers=_gh_headers(), timeout=90)
    if r.status_code != 200:
        return None, r.status_code
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        text = ""
        for name in sorted(z.namelist()):
            if name.endswith(".txt"):
                text += f"\n===== {name} =====\n" + z.read(name).decode("utf-8", "ignore")
        return "\n".join(text.splitlines()[-max_lines:]), 200
    except Exception as e:
        return f"(erro ao abrir o zip de logs: {e})", 200

def gh_get_file(path):
    r = requests.get(f"{GH_API}/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}",
                     headers=_gh_headers(), timeout=30)
    if r.status_code == 200:
        j = r.json()
        return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]
    return "", None

def gh_put_file(path, content, sha, message):
    body = {"message": message, "branch": BRANCH,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    return requests.put(f"{GH_API}/repos/{OWNER}/{REPO}/contents/{path}",
                        headers=_gh_headers(), json=body, timeout=30)

# ----------------------------------------------------------------- cron-job.org helpers
def _cron_headers():
    return {"Authorization": f"Bearer {CRON_KEY}", "Content-Type": "application/json"}

def cron_list(vertical):
    """Agendamentos desta vertical (título começa com 'Clipping <Label>')."""
    r = requests.get(f"{CRON_API}/jobs", headers=_cron_headers(), timeout=30)
    if r.status_code != 200:
        return None, r
    pref = f"{CRON_PREFIX} {VERTICAIS[vertical]['label']}"
    return [j for j in r.json().get("jobs", [])
            if str(j.get("title", "")).startswith(pref)], r

def cron_create(vertical, hours, minutes, wdays, period, recipients):
    label = VERTICAIS[vertical]["label"]
    body = json.dumps({"ref": BRANCH, "inputs": {"vertical": vertical, "when": period,
                                                 "recipients": recipients}})
    job = {
        "url": f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}/dispatches",
        "enabled": True,
        "title": f"{CRON_PREFIX} {label} {hours[0]:02d}:{minutes[0]:02d} {period}",
        "requestMethod": 1, "requestTimeout": 60, "saveResponses": True,
        "schedule": {"timezone": "America/Sao_Paulo", "expiresAt": 0,
                     "hours": hours, "minutes": minutes, "mdays": [-1],
                     "months": [-1], "wdays": wdays},
        "extendedData": {"headers": {
            "Authorization": f"Bearer {PAT}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json"}, "body": body},
    }
    return requests.put(f"{CRON_API}/jobs", headers=_cron_headers(),
                        json={"job": job}, timeout=30)

def cron_delete(jid):
    return requests.delete(f"{CRON_API}/jobs/{jid}", headers=_cron_headers(), timeout=30)

def cron_job_inputs(job):
    """E-mails / período / vertical de um agendamento — ficam no corpo do POST que ele
    manda pro GitHub (`extendedData.body`). A listagem nem sempre traz esse campo,
    então busca o detalhe do job quando faltar."""
    body = ((job.get("extendedData") or {}).get("body")) or ""
    if not body:
        try:
            r = requests.get(f"{CRON_API}/jobs/{job['jobId']}", headers=_cron_headers(), timeout=20)
            if r.status_code == 200:
                det = r.json().get("jobDetails") or r.json().get("job") or {}
                body = ((det.get("extendedData") or {}).get("body")) or ""
        except Exception:
            pass
    try:
        return (json.loads(body).get("inputs") or {}) if body else {}
    except Exception:
        return {}

def cron_set_enabled(jid, enabled):
    return requests.patch(f"{CRON_API}/jobs/{jid}", headers=_cron_headers(),
                          json={"job": {"enabled": enabled}}, timeout=30)

# ----------------------------------------------------------------- widgets reutilizáveis
def email_editor(prefix, label="E-mails"):
    """Lista de e-mails com botão para adicionar/remover caixas. Devolve 'a@x.com, b@y.com'.
    Cada linha tem id próprio para o valor não 'pular' de caixa ao remover uma do meio."""
    sk, ck = f"__mails_{prefix}", f"__mailseq_{prefix}"
    if sk not in st.session_state:
        iniciais = [e.strip() for e in re.split(r"[,;\s]+", DEFAULT_TO or "") if e.strip()]
        st.session_state[ck] = 0
        st.session_state[sk] = []
        for e in (iniciais or [""]):
            st.session_state[sk].append({"id": st.session_state[ck], "v": e})
            st.session_state[ck] += 1

    st.markdown(f"**{label}**")
    linhas, remover = st.session_state[sk], None
    for i, row in enumerate(linhas):
        c1, c2 = st.columns([9, 1])
        row["v"] = c1.text_input(f"{label} {i+1}", value=row["v"],
                                 key=f"{sk}_in_{row['id']}", label_visibility="collapsed",
                                 placeholder="nome@empresa.com")
        if len(linhas) > 1 and c2.button("✕", key=f"{sk}_rm_{row['id']}", help="remover"):
            remover = i
    if remover is not None:
        linhas.pop(remover)
        st.rerun()
    if st.button("➕ Adicionar e-mail", key=f"{sk}_add"):
        linhas.append({"id": st.session_state[ck], "v": ""})
        st.session_state[ck] += 1
        st.rerun()
    return ", ".join(r["v"].strip() for r in linhas if r["v"].strip())

def period_editor(prefix, n=1, unidade="dias"):
    """Período = número digitado + unidade escolhida na lista. Devolve '1d' / '12h'."""
    st.markdown("**Período**")
    c1, c2 = st.columns([1, 1])
    qtd = c1.number_input("Quantidade", min_value=1, max_value=90, value=n, step=1,
                          key=f"{prefix}_qtd")
    uni = c2.selectbox("Unidade", ["horas", "dias"],
                       index=(1 if unidade == "dias" else 0), key=f"{prefix}_uni")
    per = f"{int(qtd)}{'d' if uni == 'dias' else 'h'}"
    st.caption(f"Busca notícias das últimas **{int(qtd)} {uni}** · enviado ao robô como `{per}`")
    return per

def file_editor(titulo, path, ajuda, altura=260):
    """Editor de um arquivo do repositório (já vem preenchido com o conteúdo atual)."""
    st.markdown(f"**{titulo}**")
    st.caption(ajuda)
    cur, sha = gh_get_file(path)
    val = st.text_area(titulo, value=cur, height=altura, key=f"ta_{path}",
                       label_visibility="collapsed")
    if sha is None:
        st.warning(f"Não consegui ler `{path}` do repositório (token/rede). "
                   "Não salve agora para não sobrescrever o conteúdo atual.")
        return
    c1, c2 = st.columns([2, 3])
    if c1.button(f"💾 Salvar", key=f"bt_{path}"):
        with st.spinner("Salvando no repositório…"):
            r = gh_put_file(path, val, sha, f"Atualiza {path} pelo app")
        if r.status_code in (200, 201):
            st.success("✅ Salvo. Vale na próxima execução do robô.")
        else:
            st.error(f"Falhou ({r.status_code}): {r.text[:300]}")
    itens = len([l for l in val.splitlines() if l.strip() and not l.lstrip().startswith("#")])
    c2.caption(f"`{path}` · {itens} linha(s) úteis")

# ----------------------------------------------------------------- UI
st.title("📰 Clipping")
st.caption("Painel de controle. Dispara o robô no GitHub (e-mail + Drive) na hora ou no horário agendado.")

if APP_PW and not st.session_state.get("_auth"):
    pw = st.text_input("🔒 Senha de acesso", type="password")
    if st.button("Entrar"):
        if pw == APP_PW:
            st.session_state["_auth"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()

if not PAT:
    st.error("Falta o secret **github_pat**. Veja o SETUP_APP.md.")
    st.stop()

# ---- seletor de vertical (cada uma tem config e agendamentos próprios)
labels = {k: f"{v['icon']} {v['label']}" for k, v in VERTICAIS.items()}
escolha = st.radio("Vertical", list(labels.values()), horizontal=True,
                   label_visibility="collapsed")
VERT = next(k for k, v in labels.items() if v == escolha)
V = VERTICAIS[VERT]
st.caption(f"Vertical **{V['label']}** · portais próprios: {V['portais']} · "
           f"config em `{V['keywords']}` / `{V['sources']}`")

tab_run, tab_cfg, tab_sched, tab_debug = st.tabs(
    ["▶️ Rodar agora", "⚙️ Config", "🕗 Agendamento", "🔧 Debug"])

with tab_run:
    to = email_editor(f"run_{VERT}")
    period = period_editor(f"run_{VERT}")
    st.write("")
    if st.button(f"📨 Rodar agora — {V['label']}"):
        with st.spinner("Disparando o GitHub Action…"):
            r = dispatch_now(VERT, period, to)
        if r.status_code == 204:
            st.success(f"✅ Disparado ({V['label']}, {period})! "
                       "O robô coleta e envia o e-mail em ~2-5 min.")
            st.link_button("Ver execução no GitHub",
                           f"https://github.com/{OWNER}/{REPO}/actions")
        else:
            st.error(f"Falhou ({r.status_code}). Verifique o PAT e as permissões. "
                     f"{r.text[:300]}")

with tab_cfg:
    st.markdown(f"Configuração da vertical **{V['label']}**. "
                "Cada save vale a partir da **próxima execução**.")
    file_editor("Palavras-chave", V["keywords"],
                "Uma por linha — termos buscados no Google News. (# = comentário)", 260)
    st.divider()
    file_editor("Fontes aceitas", V["sources"],
                "Uma por linha — só entram notícias do Google News dessas fontes "
                "(nome exato como aparece no Google News).", 260)
    st.divider()
    file_editor("Prompt da IA", V["prompt"],
                "Texto que vai junto da lista (o que você copia do e-mail e cola no Claude).", 300)

with tab_sched:
    st.markdown(f"Agendamentos de **{V['label']}** — o robô roda sozinho "
                "(cron-job.org → GitHub, **sem fila**). Pode ter **quantos quiser**.")
    if not CRON_KEY:
        st.warning("Falta o secret **cronjob_api_key** (cron-job.org). Veja o SETUP_APP.md.")
    else:
        jobs, rr = cron_list(VERT)
        if jobs is None:
            st.error(f"Não consegui falar com o cron-job.org "
                     f"(HTTP {getattr(rr, 'status_code', '?')}). Confira a `cronjob_api_key`.")
            jobs = []

        st.markdown(f"**Ativos ({len(jobs)})**")
        if not jobs:
            st.caption("Nenhum ainda — crie um abaixo.")
        for j in jobs:
            sc = j.get("schedule", {})
            hh = (sc.get("hours") or [0])[0]
            mm = (sc.get("minutes") or [0])[0]
            wd = sc.get("wdays") or [-1]
            dias_txt = ("todos os dias" if wd == [-1]
                        else ", ".join(k for k, v in DIAS.items() if v in wd))
            inp = cron_job_inputs(j)
            per = inp.get("when") or (j.get("title", "").split() or [""])[-1]
            emails = [e.strip() for e in re.split(r"[,;\s]+", inp.get("recipients") or "")
                      if "@" in e]
            on = "🟢" if j.get("enabled") else "⚪"
            c = st.columns([6, 1, 1])
            c[0].markdown(f"{on} **{hh:02d}:{mm:02d}** · {dias_txt} · `{per}`")
            if emails:
                c[0].caption("📧 " + " · ".join(emails))
            else:
                c[0].caption("📧 (usa a lista padrão do robô)")
            if c[1].button("⏸️" if j.get("enabled") else "▶️", key=f"en_{j['jobId']}",
                           help="ativar/desativar"):
                cron_set_enabled(j["jobId"], not j.get("enabled"))
                st.rerun()
            if c[2].button("🗑️", key=f"del_{j['jobId']}", help="excluir"):
                cron_delete(j["jobId"])
                st.rerun()

        st.divider()
        st.markdown("**➕ Novo agendamento**")
        t = st.time_input("Horário (BRT)", value=_dt.time(8, 0), key=f"nt_{VERT}")
        period_s = period_editor(f"sched_{VERT}")
        dias_sel = st.multiselect("Dias", list(DIAS.keys()),
                                  default=["Seg", "Ter", "Qua", "Qui", "Sex"],
                                  key=f"nd_{VERT}")
        to_s = email_editor(f"sched_{VERT}", "E-mails do agendamento")
        if st.button("➕ Criar agendamento"):
            wdays = sorted(DIAS[d] for d in dias_sel) or [-1]
            with st.spinner("Criando no cron-job.org…"):
                r = cron_create(VERT, [t.hour], [t.minute], wdays, period_s, to_s)
            if r.status_code in (200, 201):
                st.success(f"✅ Criado: {t.strftime('%H:%M')} (BRT), {period_s}, {V['label']}.")
                st.rerun()
            else:
                st.error(f"Falhou ({r.status_code}): {r.text[:300]}")

with tab_debug:
    st.markdown("**🩺 Diagnóstico de conexão**")
    if st.button("Checar conexões"):
        gh, wf = gh_check(), gh_wf_check()
        st.write("GitHub (PAT + acesso ao repo):", "✅ ok" if gh == 200 else f"❌ HTTP {gh}")
        st.write("Workflow `clipping.yml`:", "✅ encontrado" if wf == 200 else f"❌ HTTP {wf}")
        if CRON_KEY:
            jobs, rr = cron_list(VERT)
            if rr is not None and rr.status_code == 200:
                st.write("cron-job.org:", f"✅ {len(jobs)} agendamento(s) em {V['label']}")
            else:
                st.write("cron-job.org:", f"❌ HTTP {getattr(rr, 'status_code', '?')}")
        else:
            st.write("cron-job.org:", "— sem `cronjob_api_key`")
        for f in (V["keywords"], V["sources"], V["prompt"]):
            cur, _ = gh_get_file(f)
            n = len([l for l in cur.splitlines()
                     if l.strip() and not l.lstrip().startswith("#")])
            st.write(f"`{f}`:", f"✅ {n} linha(s)" if cur else "❌ não encontrado")

    st.divider()
    st.markdown("**📊 Últimas execuções**")
    runs = recent_runs(8)
    if not runs:
        st.caption("Nenhuma execução ainda (ou PAT sem acesso a Actions).")
    labels_run = {}
    for run in runs:
        ic = {"success": "✅", "failure": "❌", "cancelled": "⚪"}.get(run.get("conclusion"), "🟡")
        st.markdown(f"{ic} **{run.get('display_title', 'run')[:55]}** · "
                    f"`{run.get('status')}/{run.get('conclusion')}` · "
                    f"[abrir]({run.get('html_url')}) · "
                    f"{run.get('created_at', '')[:16].replace('T', ' ')}")
        labels_run[f"#{run.get('run_number')} · {run.get('conclusion') or run.get('status')}"] = run.get("id")

    if labels_run:
        st.divider()
        st.markdown("**📜 Ver logs no app** (debug pelo celular, sem abrir o PC)")
        sel = st.selectbox("Execução", list(labels_run.keys()))
        if st.button("Carregar logs"):
            with st.spinner("Baixando logs…"):
                txt, code = run_logs_tail(labels_run[sel])
            if txt:
                errs = [l for l in txt.splitlines()
                        if any(k in l for k in ("Error", "Traceback", "[erro]", "[aviso]",
                                                "AVISO", "Exception", "Failed", "raise"))]
                if errs:
                    st.markdown("**⚠️ Linhas com erro/aviso:**")
                    st.code("\n".join(errs[-30:]))
                with st.expander("Log completo (fim)"):
                    st.code(txt)
            else:
                st.error(f"Sem logs (HTTP {code}). A execução pode não ter terminado.")
