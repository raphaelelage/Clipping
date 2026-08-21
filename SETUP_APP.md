# Setup — App de controle (Streamlit) + Agendamento instantâneo

Arquitetura (tudo grátis):

```
App Streamlit (celular)  ──"Rodar agora"──►  GitHub workflow_dispatch  ─► Action roda JÁ
        │                                                                     │
        └──"Agendamento"──►  cron-job.org (API)  ──no horário──►  workflow_dispatch  ─► Action roda JÁ
```

O **Action** continua fazendo todo o trabalho (coleta multi-fonte + e-mail + Google Drive).
Não há `schedule:` no GitHub (que atrasa); quem agenda é o **cron-job.org**, que dispara em segundos.

---

## 1. GitHub PAT (token para disparar o Action)
1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate**.
2. **Resource owner:** sua conta · **Repository access:** Only select → `raphaelelage/Clipping`.
3. **Permissions → Repository → Actions: Read and write**.
4. Gere e copie o token (`github_pat_...`).

## 2. cron-job.org (agendador grátis)
1. Crie conta em https://cron-job.org (grátis).
2. **Console → Settings → API** → gere uma **API key**.
   *(Não precisa criar o job na mão — o app cria/edita pra você.)*

## 3. Deploy do app no Streamlit Cloud
1. https://share.streamlit.io → **New app** → repo `raphaelelage/Clipping`, arquivo `streamlit_app.py`.
2. **Settings → Secrets** → cole:
   ```toml
   github_pat        = "github_pat_xxx"
   github_owner      = "raphaelelage"
   github_repo       = "Clipping"
   workflow_file     = "clipping.yml"
   branch            = "main"
   cronjob_api_key   = "xxx"
   default_recipients = "voce@exemplo.com"
   app_password      = "uma_senha_sua"   # opcional, mas recomendado: protege o link público
   ```
3. **Customize a URL** (App settings → General → custom subdomain) para algo como `clipping-admin` → `https://clipping-admin.streamlit.app`.
4. Abra o link no celular. Se definiu `app_password`, ele pede a senha antes de mostrar o painel.

> **Segurança:** o app dispara o Action e guarda o PAT nos secrets (server-side, não aparece no browser).
> Mesmo assim, defina `app_password` — sem ela, qualquer um com o link consegue apertar "Rodar agora".

## 4. Usar
- **▶️ Rodar agora:** escolhe período + e-mails → dispara o Action na hora.
- **⚙️ Config:** edita **prompt**, **palavras-chave** (`keywords.txt`) e **fontes** (`sources.txt`) — tudo numa aba. Cada save vale na próxima execução.
- **🕗 Agendamento:** crie **quantos agendamentos quiser** (cada um com horário, dias, período e e-mails próprios). Lista os existentes com botões de ativar/desativar e excluir. Roda via cron-job.org → GitHub, **sem a fila do cron**.
- **🔧 Debug:** diagnóstico de conexões, últimas execuções e **logs do Action no app**.

> Os secrets do **e-mail** e do **Google Drive** continuam onde sempre estiveram: em
> **Settings → Secrets and variables → Actions** do repositório (EMAIL_REMETENTE, EMAIL_SENHA,
> GOOGLE_CREDENTIALS_JSON, DRIVE_FOLDER_ID). O app não precisa deles.

## Variável opcional: `SEC_CONTATO`
GitHub → Settings → Secrets and variables → Actions → aba **Variables** → `SEC_CONTATO`.
É o e-mail de contato que a SEC exige no `User-Agent` (sem ele a SEC responde HTTP 403).
Se você não criar, o robô usa o `EMAIL_REMETENTE` automaticamente; crie apenas se quiser
que a SEC receba um endereço diferente do remetente do clipping.

## Rodar no seu PC quando ele estiver ligado (e no GitHub quando não estiver)

O workflow decide sozinho, a cada execução, onde rodar. São dois modos porque a máquina muda o
que vale a pena:

| | PC ligado | PC desligado |
|---|---|---|
| Onde roda | seu computador | GitHub |
| Coleta do Google News | sequencial, 1 job | 4 robôs em paralelo |
| Por quê | um runner atende um job por vez, e todos sairiam do mesmo IP de casa | cada robô ganha uma máquina com IP próprio |

**Em caso de qualquer dúvida ele escolhe o GitHub**, que sempre funciona. O contrário — mandar o
job para uma máquina desligada — deixaria o clipping preso na fila.

### Passo 1 — instalar o runner no PC (uma vez)
1. Vá em **github.com/raphaelelage/Clipping → Settings → Actions → Runners → New self-hosted runner**
2. Escolha **Windows**, e rode no PowerShell os comandos que a própria página mostra
   (baixar, `config.cmd`, e por fim `run.cmd`)
3. Quando ele perguntar os labels, pode aceitar os padrões

Para o runner subir sozinho com o Windows, em vez de `run.cmd` instale como serviço:
```powershell
./svc.sh install
./svc.sh start
```
(no Windows: `.\svc.cmd install` e `.\svc.cmd start`, no diretório do runner)

### Passo 2 — dar ao workflow permissão para enxergar o runner
Listar runners exige um token com **Administration: Read** — permissão que o token automático do
Actions **não** tem. Sem isso o workflow sempre escolhe o GitHub (funciona, só não usa o PC).

1. **github.com/settings/personal-access-tokens/new** → fine-grained token
2. Repository access: **só o repo Clipping**
3. Repository permissions → **Administration: Read-only**
4. Copie o token e crie o secret **`RUNNER_PAT`** em Settings → Secrets and variables → Actions

### Requisitos no PC
Python 3.11+ instalado e no PATH. As dependências o próprio workflow instala.

### Como saber onde rodou
A primeira linha do job `escolher`, no log da execução, diz:
`PC ligado (1 runner livre) -> rodando local` ou `PC indisponivel -> rodando no GitHub com 4 robos`.
