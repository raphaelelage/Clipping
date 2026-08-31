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

# Verticais FIXAS (fallback) — o registro dinamico verticais.json, editavel na secao
# "Gerenciar seções" da aba Config, e carregado por cima em carregar_verticais_app().
VERTICAIS_FIXAS = {
    # combinada: keywords/fontes sao a UNIAO de saude+educacao (sem arquivo proprio)
    "saude_educacao": {"label": "Saúde e Educação", "icon": "📊",
                       "keywords": None, "sources": None,
                       "prompt": "ai_prompt_saude_educacao.txt",
                       "portais": "ANS · Anvisa · MEC · Capes"},
    "saude": {"label": "Saúde", "icon": "🏥",
              "keywords": "keywords_saude.txt", "sources": "sources_saude.txt",
              "prompt": "ai_prompt_saude.txt", "portais": "ANS · Anvisa"},
    "educacao": {"label": "Educação", "icon": "🎓",
                 "keywords": "keywords_educacao.txt", "sources": "sources_educacao.txt",
                 "prompt": "ai_prompt_educacao.txt", "portais": "MEC · Capes"},
}
_PORTAIS_BASE = {"saude": "ANS · Anvisa", "educacao": "MEC · Capes"}


def montar_verticais(registro_json):
    """Dict de verticais do app a partir do verticais.json (custom herdam estrutura)."""
    import json as _json
    out = {k: dict(v) for k, v in VERTICAIS_FIXAS.items()}
    try:
        reg = _json.loads(registro_json) if registro_json else {}
    except Exception:
        return out
    for chave, cfg in reg.items():
        bases = [b for b in (cfg.get("herda") or []) if b]
        if chave in out:
            out[chave]["label"] = cfg.get("label") or out[chave]["label"]
            out[chave]["icon"] = cfg.get("icon") or out[chave]["icon"]
            continue
        if not bases:
            continue
        out[chave] = {
            "label": cfg.get("label") or chave, "icon": cfg.get("icon") or "🗂️",
            "keywords": f"keywords_{chave}.txt", "sources": f"sources_{chave}.txt",
            "prompt": f"ai_prompt_{chave}.txt",
            "portais": " · ".join(_PORTAIS_BASE.get(b, b) for b in bases),
            "herda": bases, "custom": True,
        }
    return out

class _SemRede:
    """Resposta falsa: erro de rede NUNCA pode derrubar o app (o Streamlit executa o
    codigo de todas as abas a cada clique — uma falha numa aba quebrava a tela inteira)."""
    def __init__(self, erro=""):
        self.status_code, self.text = 0, f"sem conexão com o serviço. {erro}"[:200]
    def json(self):
        return {}

def _req(metodo, url, **kw):
    try:
        return requests.request(metodo, url, timeout=kw.pop("timeout", 30), **kw)
    except Exception as e:
        return _SemRede(type(e).__name__)

# ----------------------------------------------------------------- GitHub helpers
def _gh_headers():
    return {"Authorization": f"Bearer {PAT}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}

def dispatch_now(vertical, period, recipients):
    url = f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}/dispatches"
    body = {"ref": BRANCH, "inputs": {"vertical": vertical, "when": period,
                                      "recipients": recipients}}
    return _req("post", url, headers=_gh_headers(), json=body, timeout=30)

def recent_runs(n=8):
    r = _req("get", f"{GH_API}/repos/{OWNER}/{REPO}/actions/runs?per_page={n}",
                     headers=_gh_headers(), timeout=30)
    return r.json().get("workflow_runs", []) if r.status_code == 200 else []

def gh_check():
    return _req("get", f"{GH_API}/repos/{OWNER}/{REPO}", headers=_gh_headers(),
                        timeout=20).status_code

def gh_wf_check():
    return _req("get", f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}",
                        headers=_gh_headers(), timeout=20).status_code

def run_logs_tail(run_id, max_lines=250):
    """Baixa o .zip de logs da execução e devolve as últimas linhas (debug pelo celular)."""
    import io, zipfile
    r = _req("get", f"{GH_API}/repos/{OWNER}/{REPO}/actions/runs/{run_id}/logs",
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
    if not path:
        return "", None
    r = _req("get", f"{GH_API}/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}",
             headers=_gh_headers(), timeout=30)
    try:
        if r.status_code == 200:
            j = r.json()
            return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]
    except Exception:
        pass
    return "", None

def gh_put_file(path, content, sha, message):
    body = {"message": message, "branch": BRANCH,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    return _req("put", f"{GH_API}/repos/{OWNER}/{REPO}/contents/{path}",
                        headers=_gh_headers(), json=body, timeout=30)

# ----------------------------------------------------------------- cron-job.org helpers
def _cron_headers():
    return {"Authorization": f"Bearer {CRON_KEY}", "Content-Type": "application/json"}

def _job_da_vertical(j, vertical):
    """O título do job identifica a vertical. Formato atual: 'Clipping [chave] HH:MM per'.
    Aceita também o formato antigo ('Clipping <Label> ...'), tomando cuidado para
    'Saúde e Educação' não ser confundido com 'Saúde' (um é prefixo do outro)."""
    t = str(j.get("title", ""))
    if t.startswith(f"{CRON_PREFIX} ["):
        return t.startswith(f"{CRON_PREFIX} [{vertical}]")
    label = VERTICAIS[vertical]["label"]
    if not t.startswith(f"{CRON_PREFIX} {label} "):
        return False
    for k, v in VERTICAIS.items():          # outra vertical cujo label comeca igual?
        if k != vertical and len(v["label"]) > len(label)                 and t.startswith(f"{CRON_PREFIX} {v['label']} "):
            return False
    return True

@st.cache_data(ttl=60, show_spinner=False)
def _cron_jobs(_v=0):
    """Busca a lista de jobs UMA vez por minuto. Sem isso o app estourava o limite do
    cron-job.org (HTTP 429): o Streamlit reexecuta o script inteiro a cada clique."""
    r = _req("get", f"{CRON_API}/jobs", headers=_cron_headers(), timeout=30)
    if r.status_code != 200:
        return None, r.status_code
    try:
        return r.json().get("jobs", []), 200
    except Exception:
        return None, r.status_code

def cron_invalidar():
    _cron_jobs.clear()

def cron_list(vertical):
    """Agendamentos desta vertical (a partir da lista em cache)."""
    jobs, code = _cron_jobs()
    if jobs is None:
        return None, code
    return [j for j in jobs if _job_da_vertical(j, vertical)], code

def _montar_job(vertical, hours, minutes, wdays, period, recipients, enabled=True):
    body = json.dumps({"ref": BRANCH, "inputs": {"vertical": vertical, "when": period,
                                                 "recipients": recipients}})
    return {
        "url": f"{GH_API}/repos/{OWNER}/{REPO}/actions/workflows/{WF}/dispatches",
        "enabled": enabled,
        "title": f"{CRON_PREFIX} [{vertical}] {hours[0]:02d}:{minutes[0]:02d} {period}",
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

def cron_create(vertical, hours, minutes, wdays, period, recipients):
    return _req("put", f"{CRON_API}/jobs", headers=_cron_headers(),
                        json={"job": _montar_job(vertical, hours, minutes, wdays,
                                                 period, recipients)}, timeout=30)

def cron_update(jid, vertical, hours, minutes, wdays, period, recipients, enabled=True):
    """Edita um agendamento existente (mesma estrutura do create, via PATCH)."""
    return _req("patch", f"{CRON_API}/jobs/{jid}", headers=_cron_headers(),
                          json={"job": _montar_job(vertical, hours, minutes, wdays,
                                                   period, recipients, enabled)}, timeout=30)

def cron_delete(jid):
    return _req("delete", f"{CRON_API}/jobs/{jid}", headers=_cron_headers(), timeout=30)

CRON_STATUS = {0: "nunca executou", 1: "✅ OK", 2: "❌ falhou (DNS)",
               3: "❌ falhou (conexão)", 4: "❌ falhou (HTTP)", 5: "❌ falhou (timeout)",
               6: "❌ falhou (resposta grande)", 7: "❌ falhou (URL inválida)",
               8: "❌ falhou (erro interno)", 9: "❌ falhou"}

def cron_historico(jid):
    """Últimas execuções + próximas previstas. É o que diz se o agendamento disparou
    e o que o GitHub respondeu (204 = aceito; 401/404 = token ou repo errado)."""
    try:
        r = _req("get", f"{CRON_API}/jobs/{jid}/history", headers=_cron_headers(), timeout=20)
        if r.status_code == 200:
            j = r.json()
            return (j.get("history") or []), (j.get("predictions") or [])
    except Exception:
        pass
    return [], []

def _ts(unix):
    try:
        return _dt.datetime.fromtimestamp(int(unix), _dt.timezone(_dt.timedelta(hours=-3))
                                          ).strftime("%d/%m %H:%M")
    except Exception:
        return "?"

def cron_job_inputs(job):
    """E-mails / período / vertical de um agendamento — ficam no corpo do POST que ele
    manda pro GitHub (`extendedData.body`). A listagem nem sempre traz esse campo,
    então busca o detalhe do job quando faltar."""
    body = ((job.get("extendedData") or {}).get("body")) or ""
    try:
        return (json.loads(body).get("inputs") or {}) if body else {}
    except Exception:
        return {}

def cron_set_enabled(jid, enabled):
    return _req("patch", f"{CRON_API}/jobs/{jid}", headers=_cron_headers(),
                          json={"job": {"enabled": enabled}}, timeout=30)

# ----------------------------------------------------------------- widgets reutilizáveis
def email_editor(prefix, label="E-mails", valores=None):
    """Lista de e-mails com botão para adicionar/remover caixas. Devolve 'a@x.com, b@y.com'.
    Cada linha tem id próprio para o valor não 'pular' de caixa ao remover uma do meio.
    `valores`: string inicial (usado ao editar um agendamento já existente)."""
    sk, ck = f"__mails_{prefix}", f"__mailseq_{prefix}"
    if sk not in st.session_state:
        base = DEFAULT_TO if valores is None else valores
        iniciais = [e.strip() for e in re.split(r"[,;\s]+", base or "") if e.strip()]
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

# ---- verticais dinamicas + seletor (cada uma tem config e agendamentos próprios)
_REG_RAW, _REG_SHA = gh_get_file("verticais.json")
VERTICAIS = montar_verticais(_REG_RAW)
labels = {k: f"{v['icon']} {v['label']}" for k, v in VERTICAIS.items()}
escolha = st.radio("Vertical", list(labels.values()), horizontal=True,
                   label_visibility="collapsed")
VERT = next(k for k, v in labels.items() if v == escolha)
V = VERTICAIS[VERT]

with st.expander("🗂️ Gerenciar seções (renomear · criar · excluir)"):
    import json as _json
    try:
        _reg = _json.loads(_REG_RAW) if _REG_RAW else {}
    except Exception:
        _reg = {}
    st.caption("As seções viram opções aqui no app e valem como `vertical` nos "
               "agendamentos. Seções novas herdam portais/DOU/CVM/RSS das bases "
               "escolhidas; keywords, fontes, prompt e empresas são próprios "
               "(vazios = herda também).")

    c1, c2 = st.columns([3, 2])
    novo_nome = c1.text_input("Renomear a seção atual", value=V["label"],
                              key="ren_secao")
    if c2.button("💾 Renomear", key="bt_ren") and novo_nome.strip():
        _reg.setdefault(VERT, {"herda": V.get("herda")
                               or {"saude_educacao": ["saude", "educacao"],
                                   "saude": ["saude"],
                                   "educacao": ["educacao"]}.get(VERT, ["saude"])})
        _reg[VERT]["label"] = novo_nome.strip()
        r = gh_put_file("verticais.json",
                        _json.dumps(_reg, ensure_ascii=False, indent=2),
                        _REG_SHA, f"Renomeia seção {VERT} pelo app")
        st.success("✅ Renomeada — recarregue a página.") if r.status_code in (200, 201)             else st.error(f"Falhou ({r.status_code})")

    st.divider()
    c1, c2, c3 = st.columns([3, 3, 2])
    criar_nome = c1.text_input("Nova seção (nome)", key="nv_nome",
                               placeholder="ex.: Farmácias")
    criar_herda = c2.multiselect(
        "Consolida / herda de", [k for k in VERTICAIS if k != VERT],
        default=["saude"], key="nv_herda",
        help="Pode apontar para QUALQUER seção (inclusive as criadas aqui): a nova seção "
             "consolida as listas delas e herda a estrutura (portais/DOU/CVM) das bases "
             "de origem. Ex.: uma seção que consolida 'farma' + 'hospitais'.")
    if c3.button("➕ Criar", key="bt_criar") and criar_nome.strip() and criar_herda:
        import re as _re, unicodedata as _ud
        chave = _re.sub(r"[^a-z0-9]+", "_",
                        "".join(ch for ch in _ud.normalize("NFKD", criar_nome.lower())
                                if not _ud.combining(ch))).strip("_")
        if not chave or chave in VERTICAIS:
            st.error("Nome inválido ou já existe.")
        else:
            _reg[chave] = {"label": criar_nome.strip(), "icon": "🗂️",
                           "herda": criar_herda}
            r = gh_put_file("verticais.json",
                            _json.dumps(_reg, ensure_ascii=False, indent=2),
                            _REG_SHA, f"Cria seção {chave} pelo app")
            if r.status_code in (200, 201):
                st.success(f"✅ Seção **{criar_nome}** criada (chave `{chave}`) — "
                           "recarregue a página. Enquanto as listas dela estiverem "
                           "vazias, ela usa as da herança.")
            else:
                st.error(f"Falhou ({r.status_code})")

    if V.get("custom"):
        st.divider()
        if st.button(f"🗑️ Excluir a seção {V['label']}", key="bt_excluir"):
            _reg.pop(VERT, None)
            r = gh_put_file("verticais.json",
                            _json.dumps(_reg, ensure_ascii=False, indent=2),
                            _REG_SHA, f"Exclui seção {VERT} pelo app")
            st.success("✅ Excluída — recarregue a página.") if r.status_code in (200, 201)                 else st.error(f"Falhou ({r.status_code})")
if V["keywords"]:
    st.caption(f"Vertical **{V['label']}** · portais: {V['portais']} · "
               f"config em `{V['keywords']}` / `{V['sources']}`")
else:
    st.caption(f"Vertical **{V['label']}** · portais: {V['portais']} · "
               "palavras-chave e fontes = **união de Saúde + Educação** (automática)")

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
    if V["keywords"]:
        file_editor("Palavras-chave", V["keywords"],
                    "Uma por linha — termos buscados no Google News. (# = comentário)", 260)
        st.divider()
        file_editor("Fontes aceitas", V["sources"],
                    "Uma por linha — só entram notícias do Google News dessas fontes "
                    "(nome exato como aparece no Google News).", 260)
        st.divider()
    else:
        st.info("**Palavras-chave e fontes desta seção são automáticas:** ela usa a união "
                "das listas de **Saúde** e **Educação**. Para mudar, edite uma dessas duas "
                "seções — o efeito aparece aqui na próxima execução.")
        st.caption("Listas usadas: `keywords_saude.txt` + `keywords_educacao.txt` e "
                   "`sources_saude.txt` + `sources_educacao.txt` (duplicatas removidas).")
        st.divider()
    file_editor("Prompt da IA", V["prompt"],
                "Texto que vai junto da lista (o que você copia do e-mail e cola no Claude).", 300)
    st.divider()
    if VERT == "saude_educacao":
        st.info("**Empresas do summary de valuation:** esta seção usa a **união** das "
                "listas de Saúde e Educação — edite lá.")
    else:
        file_editor("Empresas do summary de valuation",
                    f"empresas_valuation_{VERT}.txt",
                    "Um ticker Yahoo por linha (B3 = XXXX3.SA; ADR = ticker dos EUA, ex. "
                    "AFYA). A tabela sai no topo do e-mail; lista vazia = sem tabela"
                    + (" (herda da base)." if V.get("custom") else "."), 220)

with tab_sched:
    st.markdown(f"Agendamentos de **{V['label']}** — o robô roda sozinho "
                "(cron-job.org → GitHub, **sem fila**). Pode ter **quantos quiser**.")
    if not CRON_KEY:
        st.warning("Falta o secret **cronjob_api_key** (cron-job.org). Veja o SETUP_APP.md.")
    else:
        jobs, rr = cron_list(VERT)
        if jobs is None:
            if rr == 429:
                st.warning("O cron-job.org limitou as consultas (HTTP 429). "
                           "Aguarde ~1 minuto e recarregue — os agendamentos existentes "
                           "continuam funcionando normalmente.")
            else:
                st.error(f"Não consegui falar com o cron-job.org (HTTP {rr}). "
                         "Confira a `cronjob_api_key`.")
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
            c = st.columns([6, 1, 1, 1])
            c[0].markdown(f"{on} **{hh:02d}:{mm:02d}** · {dias_txt} · `{per}`")
            if c[1].button("✏️", key=f"ed_{j['jobId']}", help="editar"):
                st.session_state["_editando"] = j["jobId"]
                for k in list(st.session_state):        # limpa o form anterior
                    if k.startswith(f"__mails_edit_{j['jobId']}"):
                        del st.session_state[k]
                st.rerun()
            if emails:
                c[0].caption("📧 " + " · ".join(emails))
            else:
                c[0].caption("📧 (usa a lista padrão do robô)")
            # diagnostico sob demanda (cada consulta e uma chamada a mais na API)
            ver = st.session_state.get(f"_diag_{j['jobId']}", False)
            hist, prev = cron_historico(j["jobId"]) if ver else ([], [])
            ult = hist[0] if hist else None
            if ult:
                cod = ult.get("httpStatus")
                extra = (" — GitHub respondeu 204 (aceito)" if cod == 204
                         else (f" — GitHub respondeu {cod}" if cod else ""))
                c[0].caption(f"⏱️ última: {_ts(ult.get('date'))} · "
                             f"{CRON_STATUS.get(ult.get('status'), '?')}{extra}")
            else:
                st_j = j.get("lastStatus", 0)
                c[0].caption(f"⏱️ {CRON_STATUS.get(st_j, 'sem histórico')}"
                             + (f" · {_ts(j.get('lastExecution'))}" if j.get("lastExecution") else ""))
            if prev:
                c[0].caption(f"⏭️ próxima: {_ts(prev[0])}")
            if not ver:
                if c[0].button("🔍 ver diagnóstico", key=f"dg_{j['jobId']}"):
                    st.session_state[f"_diag_{j['jobId']}"] = True
                    st.rerun()
            if ver:
                tzj = sc.get("timezone") or "(não informado)"
                st.write(f"**Fuso do job:** `{tzj}`"
                         + ("  ⚠️ deveria ser America/Sao_Paulo — se estiver UTC, o horário "
                            "sai 3h adiantado" if tzj != "America/Sao_Paulo" else "  ✅"))
                st.write(f"**Ativo:** {j.get('enabled')} · **Última execução:** "
                         f"{_ts(j.get('lastExecution')) if j.get('lastExecution') else 'nunca'}")
                st.json({"schedule": sc, "url": j.get("url"),
                         "requestMethod": j.get("requestMethod"),
                         "inputs": inp,
                         "ultimas_execucoes": [
                             {"quando": _ts(h.get("date")), "status": h.get("status"),
                              "httpStatus": h.get("httpStatus")} for h in hist[:5]]})
            if c[2].button("⏸️" if j.get("enabled") else "▶️", key=f"en_{j['jobId']}",
                           help="ativar/desativar"):
                cron_set_enabled(j["jobId"], not j.get("enabled"))
                cron_invalidar()
                st.rerun()
            if c[3].button("🗑️", key=f"del_{j['jobId']}", help="excluir"):
                cron_delete(j["jobId"])
                cron_invalidar()
                st.rerun()

            if st.session_state.get("_editando") == j["jobId"]:
                with st.container(border=True):
                    st.markdown("**✏️ Editando este agendamento**")
                    e1, e2 = st.columns(2)
                    et = e1.time_input("Horário (BRT)", value=_dt.time(hh, mm),
                                       key=f"et_{j['jobId']}")
                    eper = e2.text_input("Período", value=per, key=f"ep_{j['jobId']}",
                                         help="Ex.: 1h, 12h, 1d, 3d")
                    dias_atuais = ([k for k, v in DIAS.items() if v in wd] if wd != [-1]
                                   else list(DIAS.keys()))
                    edias = st.multiselect("Dias", list(DIAS.keys()), default=dias_atuais,
                                           key=f"ed2_{j['jobId']}")
                    eto = email_editor(f"edit_{j['jobId']}", "E-mails",
                                       valores=inp.get("recipients") or "")
                    g1, g2 = st.columns(2)
                    if g1.button("💾 Salvar alterações", key=f"sv_{j['jobId']}"):
                        ewd = sorted(DIAS[d] for d in edias) or [-1]
                        with st.spinner("Atualizando no cron-job.org…"):
                            r = cron_update(j["jobId"], VERT, [et.hour], [et.minute], ewd,
                                            eper.strip(), eto, bool(j.get("enabled")))
                        if r.status_code in (200, 201):
                            st.session_state.pop("_editando", None)
                            cron_invalidar()
                            st.success("✅ Agendamento atualizado.")
                            st.rerun()
                        else:
                            st.error(f"Falhou ({r.status_code}): {r.text[:300]}")
                    if g2.button("Cancelar", key=f"cc_{j['jobId']}"):
                        st.session_state.pop("_editando", None)
                        st.rerun()

        st.divider()
        st.markdown("**➕ Novo agendamento**")
        t = st.time_input("Horário (BRT)", value=_dt.time(8, 0), key=f"nt_{VERT}")
        _agora = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3)))
        if (t.hour, t.minute) <= (_agora.hour, _agora.minute):
            st.caption(f"⏳ **{t.strftime('%H:%M')} já passou hoje** (agora são "
                       f"{_agora.strftime('%H:%M')} em Brasília). O cron não roda retroativo — "
                       "a 1ª execução será no próximo dia selecionado.")
        period_s = period_editor(f"sched_{VERT}")
        dias_sel = st.multiselect("Dias", list(DIAS.keys()),
                                  default=["Seg", "Ter", "Qua", "Qui", "Sex"],
                                  key=f"nd_{VERT}")
        to_s = email_editor(f"sched_{VERT}", "E-mails do agendamento")
        # trava anti-duplicata: se a criacao devolve erro (ex.: 429) mas chegou a ser
        # criada no servidor, um novo clique gerava DOIS agendamentos no mesmo horario
        # — e dois e-mails por dia. Ja aconteceu.
        dup = [j for j in jobs
               if (j.get("schedule", {}).get("hours") or [None])[0] == t.hour
               and (j.get("schedule", {}).get("minutes") or [None])[0] == t.minute]
        forcar = False
        if dup:
            st.warning(f"⚠️ Já existe um agendamento de **{V['label']}** às "
                       f"**{t.strftime('%H:%M')}**. Criar outro faria o robô rodar duas "
                       "vezes e mandar dois e-mails. Prefira **✏️ editar** o existente.")
            forcar = st.checkbox("Quero criar mesmo assim (dois no mesmo horário)",
                                 key=f"dup_{VERT}")
        if st.button("➕ Criar agendamento", disabled=bool(dup) and not forcar):
            wdays = sorted(DIAS[d] for d in dias_sel) or [-1]
            with st.spinner("Criando no cron-job.org…"):
                r = cron_create(VERT, [t.hour], [t.minute], wdays, period_s, to_s)
            if r.status_code in (200, 201):
                cron_invalidar()
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
            if rr == 200:
                st.write("cron-job.org:", f"✅ {len(jobs)} agendamento(s) em {V['label']}")
            else:
                st.write("cron-job.org:", f"❌ HTTP {rr}")
        else:
            st.write("cron-job.org:", "— sem `cronjob_api_key`")
        for f in [x for x in (V["keywords"], V["sources"], V["prompt"]) if x]:
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
