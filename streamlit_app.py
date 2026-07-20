"""
streamlit_app.py — Painel de controle do Clipping.

NÃO roda a coleta aqui dentro. Ele apenas:
  • dispara o GitHub Action na hora (workflow_dispatch)  -> "Rodar agora"
  • cria/edita um job no cron-job.org (grátis) que dispara o Action no horário escolhido
    -> "Agendamento" (instantâneo, sem a fila do cron do GitHub)

Secrets (.streamlit/secrets.toml ou painel do Streamlit Cloud):

    github_pat        = "github_pat_xxx"     # Fine-grained PAT, permissão Actions: Read and write no repo
    github_owner      = "raphaelelage"
    github_repo       = "Clipping"
    workflow_file     = "clipping.yml"
    branch            = "main"
    cronjob_api_key   = "xxx"                # API key do cron-job.org (Console -> Settings -> API)
    default_recipients = "voce@exemplo.com"
"""
import base64
import json
import requests
import streamlit as st

st.set_page_config(page_title="Clipping", page_icon="📰", layout="centered")

ACCENT = "#CC092F"
st.markdown(f"""
<style>
  .stButton>button {{ background:{ACCENT};color:#fff;border:0;border-radius:10px;
     padding:.6rem 1rem;font-weight:600;width:100%; }}
  .stButton>button:hover {{ background:#a30724;color:#fff; }}
  .block-container {{ max-width:760px;padding-top:1.5rem; }}
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

PERIODS = ["1h", "3h", "6h", "12h", "1d", "2d", "3d", "7d"]
DIAS = {"Seg": 1, "Ter": 2, "Qua": 3, "Qui": 4, "Sex": 5, "Sáb": 6, "Dom": 0}
CRON_JOB_PREFIX = "Clipping"   # todo agendamento começa com isso no cron-job.org
GH_API = "https://api.github.com"
CRON_API = "https://api.cron-job.org"

# ----------------------------------------------------------------- GitHub helpers
def _gh_headers():
    return {"Authorization": f"Bearer {PAT}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}

def dispatch_now(period, recipients):
    url = f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}/dispatches"
    body = {"ref": BRANCH, "inputs": {"when": period, "recipients": recipients}}
    r = requests.post(url, headers=_gh_headers(), json=body, timeout=30)
    return r

def recent_runs(n=5):
    url = f"{GH_API}/repos/{OWNER}/{REPO}/actions/runs?per_page={n}"
    r = requests.get(url, headers=_gh_headers(), timeout=30)
    if r.status_code != 200:
        return []
    return r.json().get("workflow_runs", [])

# ----------------------------------------------------------------- cron-job.org helpers
def _cron_headers():
    return {"Authorization": f"Bearer {CRON_KEY}", "Content-Type": "application/json"}

def cron_list_clip():
    """Lista TODOS os agendamentos do clipping (título começa com o prefixo)."""
    r = requests.get(f"{CRON_API}/jobs", headers=_cron_headers(), timeout=30)
    if r.status_code != 200:
        return None, r
    jobs = [j for j in r.json().get("jobs", [])
            if str(j.get("title", "")).startswith(CRON_JOB_PREFIX)]
    return jobs, r

def _build_job(title, hours, minutes, wdays, period, recipients):
    dispatch_url = f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}/dispatches"
    body = json.dumps({"ref": BRANCH, "inputs": {"when": period, "recipients": recipients}})
    return {
        "url": dispatch_url, "enabled": True, "title": title,
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

def cron_create(title, hours, minutes, wdays, period, recipients):
    job = _build_job(title, hours, minutes, wdays, period, recipients)
    return requests.put(f"{CRON_API}/jobs", headers=_cron_headers(), json={"job": job}, timeout=30)

def cron_delete(jid):
    return requests.delete(f"{CRON_API}/jobs/{jid}", headers=_cron_headers(), timeout=30)

def cron_set_enabled(jid, enabled):
    return requests.patch(f"{CRON_API}/jobs/{jid}", headers=_cron_headers(),
                          json={"job": {"enabled": enabled}}, timeout=30)

# ----------------------------------------------------------------- debug helpers
def gh_check():
    return requests.get(f"{GH_API}/repos/{OWNER}/{REPO}", headers=_gh_headers(), timeout=20).status_code

def gh_wf_check():
    return requests.get(f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}",
                        headers=_gh_headers(), timeout=20).status_code

def run_logs_tail(run_id, max_lines=250):
    """Baixa o .zip de logs da execução e devolve as últimas linhas (pra debugar no celular)."""
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

# ----------------------------------------------------------------- arquivo no repo (prompt da IA)
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

# ----------------------------------------------------------------- UI
st.title("📰 Clipping — Saúde & Educação")
st.caption("Painel de controle. Dispara o robô no GitHub (e-mail + Drive) na hora ou no horário agendado.")

# Trava de senha opcional (defina o secret 'app_password' para proteger o link público)
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

tab_run, tab_cfg, tab_sched, tab_debug = st.tabs(
    ["▶️ Rodar agora", "⚙️ Config", "🕗 Agendamento", "🔧 Debug"])

with tab_run:
    to = st.text_input("E-mails (vírgula) — vazio usa a lista padrão", value=DEFAULT_TO)
    period = st.text_input("Período", value="1d",
                           help="Janela de tempo. Nh = horas, Nd = dias.")
    st.caption("Exemplos: `1h`, `6h`, `12h`, `1d`, `2d`, `7d`")
    if st.button("📨 Rodar agora"):
        with st.spinner("Disparando o GitHub Action…"):
            r = dispatch_now(period, to.strip())
        if r.status_code == 204:
            st.success("✅ Disparado! O robô vai coletar e enviar o e-mail em ~1-3 min.")
            st.link_button("Ver execução no GitHub",
                           f"https://github.com/{OWNER}/{REPO}/actions")
        else:
            st.error(f"Falhou ({r.status_code}). Verifique o PAT e as permissões. {r.text[:300]}")

with tab_cfg:
    st.markdown("Edite **prompt**, **palavras-chave** e **fontes** aqui. "
                "Cada um vale a partir da **próxima execução** do robô.")

    def _editor(titulo, path, ajuda, h=240):
        st.markdown(f"**{titulo}**")
        st.caption(ajuda)
        cur, sha = gh_get_file(path)
        val = st.text_area(titulo, value=cur, height=h, key=f"ta_{path}",
                           label_visibility="collapsed")
        if st.button(f"💾 Salvar {titulo}", key=f"bt_{path}"):
            with st.spinner("Salvando no repositório…"):
                r = gh_put_file(path, val, sha, f"Atualiza {path} pelo app")
            if r.status_code in (200, 201):
                st.success("✅ Salvo. Vale na próxima execução.")
            else:
                st.error(f"Falhou ({r.status_code}): {r.text[:300]}")

    _editor("Prompt da IA", "ai_prompt.txt",
            "Texto que vai junto da lista (o que você copia do e-mail e cola no Claude).", 300)
    st.divider()
    _editor("Palavras-chave", "keywords.txt",
            "Uma por linha — termos buscados no Google News. (# = comentário)", 240)
    st.divider()
    _editor("Fontes aceitas", "sources.txt",
            "Uma por linha — só entram notícias do Google News dessas fontes (nome exato).", 240)

with tab_sched:
    st.markdown("Agendamentos em que o robô roda **sozinho** (cron-job.org → GitHub, **sem fila**). "
                "Pode ter **quantos quiser**.")
    if not CRON_KEY:
        st.warning("Falta o secret **cronjob_api_key** (cron-job.org). Veja o SETUP_APP.md.")
    else:
        import datetime as _dt
        jobs, rr = cron_list_clip()
        if jobs is None:
            st.error(f"Não consegui falar com o cron-job.org (HTTP {getattr(rr,'status_code','?')}). "
                     "Confira a `cronjob_api_key`.")
            jobs = []

        st.markdown(f"**Agendamentos ({len(jobs)})**")
        if not jobs:
            st.caption("Nenhum ainda — crie um abaixo.")
        for j in jobs:
            sc = j.get("schedule", {})
            hh = (sc.get("hours") or [0])[0]; mm = (sc.get("minutes") or [0])[0]
            wd = sc.get("wdays") or [-1]
            dias_txt = "todos os dias" if wd == [-1] else ", ".join(k for k, v in DIAS.items() if v in wd)
            on = "🟢" if j.get("enabled") else "⚪"
            cols = st.columns([6, 1, 1])
            cols[0].markdown(f"{on} **{hh:02d}:{mm:02d}** · {dias_txt}")
            if cols[1].button("⏸️" if j.get("enabled") else "▶️", key=f"en_{j['jobId']}",
                              help="ativar/desativar"):
                cron_set_enabled(j["jobId"], not j.get("enabled")); st.rerun()
            if cols[2].button("🗑️", key=f"del_{j['jobId']}", help="excluir"):
                cron_delete(j["jobId"]); st.rerun()

        st.divider()
        st.markdown("**➕ Novo agendamento**")
        c1, c2 = st.columns(2)
        with c1:
            t = st.time_input("Horário (BRT)", value=_dt.time(8, 0), key="nt")
        with c2:
            period_s = st.text_input("Período", value="1d", key="nps", help="Ex.: 1h, 12h, 1d, 3d")
        dias_sel = st.multiselect("Dias", list(DIAS.keys()),
                                  default=["Seg", "Ter", "Qua", "Qui", "Sex"], key="nd")
        to_s = st.text_input("E-mails", value=DEFAULT_TO, key="nto")
        if st.button("➕ Criar agendamento"):
            wdays = sorted(DIAS[d] for d in dias_sel) or [-1]
            title = f"{CRON_JOB_PREFIX} {t.strftime('%H:%M')} {period_s}"
            with st.spinner("Criando no cron-job.org…"):
                r = cron_create(title, [t.hour], [t.minute], wdays, period_s, to_s.strip())
            if r.status_code in (200, 201):
                st.success(f"✅ Criado: {t.strftime('%H:%M')} (BRT), período {period_s}."); st.rerun()
            else:
                st.error(f"Falhou ({r.status_code}): {r.text[:300]}")

with tab_debug:
    st.markdown("**🩺 Diagnóstico de conexão**")
    if st.button("Checar conexões"):
        gh, wf = gh_check(), gh_wf_check()
        st.write("GitHub (PAT + acesso ao repo):", "✅ ok" if gh == 200 else f"❌ HTTP {gh}")
        st.write("Workflow `clipping.yml`:", "✅ encontrado" if wf == 200 else f"❌ HTTP {wf}")
        if CRON_KEY:
            jobs, rr = cron_list_clip()
            if rr is not None and rr.status_code == 200:
                st.write("cron-job.org:", f"✅ {len(jobs)} agendamento(s)")
            else:
                st.write("cron-job.org:", f"❌ HTTP {getattr(rr,'status_code','?')}")
        else:
            st.write("cron-job.org:", "— sem `cronjob_api_key`")

    st.divider()
    st.markdown("**📊 Últimas execuções**")
    runs = recent_runs(8)
    if not runs:
        st.caption("Nenhuma execução ainda (ou PAT sem acesso a Actions).")
    labels = {}
    for run in runs:
        ic = {"success": "✅", "failure": "❌", "cancelled": "⚪"}.get(run.get("conclusion"), "🟡")
        st.markdown(f"{ic} **{run.get('display_title','run')[:55]}** · "
                    f"`{run.get('status')}/{run.get('conclusion')}` · "
                    f"[abrir]({run.get('html_url')}) · {run.get('created_at','')[:16].replace('T',' ')}")
        labels[f"#{run.get('run_number')} · {run.get('conclusion') or run.get('status')}"] = run.get("id")

    if labels:
        st.divider()
        st.markdown("**📜 Ver logs no app** (debug pelo celular, sem abrir o PC)")
        sel = st.selectbox("Execução", list(labels.keys()))
        if st.button("Carregar logs"):
            with st.spinner("Baixando logs…"):
                txt, code = run_logs_tail(labels[sel])
            if txt:
                errs = [l for l in txt.splitlines()
                        if any(k in l for k in ("Error", "Traceback", "[erro]", "Exception", "Failed", "raise"))]
                if errs:
                    st.markdown("**⚠️ Linhas com erro:**")
                    st.code("\n".join(errs[-30:]))
                with st.expander("Log completo (fim)"):
                    st.code(txt)
            else:
                st.error(f"Sem logs (HTTP {code}). A execução pode ainda não ter terminado.")
