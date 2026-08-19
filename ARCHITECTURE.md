# ARQUITETURA — leia isto primeiro

> **Para o Claude (ou quem for editar):** este arquivo é o mapa do projeto. Lendo só ele + o
> arquivo que você vai mexer, dá pra entender e alterar qualquer parte sem carregar o repo inteiro.
> Cada arquivo é pequeno e tem uma responsabilidade única.

## O que o projeto faz
Robô diário de clipping de notícias (saúde + educação, equity research). Coleta de várias fontes,
decodifica os links, monta um e-mail, anexa XLSX, sincroniza com o Google Drive
e (opcional) mantém um backlog. Roda no **GitHub Actions**; é controlado por um **app Streamlit** (celular).

## Verticais (Saúde e Educação)
O robô roda **uma vertical por execução** (`VERTICAL=saude|educacao`, input do workflow / seletor no app).
Cada vertical tem arquivos e pasta no Drive próprios:

| | Saúde | Educação |
|---|---|---|
| Palavras-chave | `keywords_saude.txt` | `keywords_educacao.txt` |
| Fontes | `sources_saude.txt` | `sources_educacao.txt` |
| Prompt | `ai_prompt_saude.txt` | `ai_prompt_educacao.txt` |
| Portais gov.br | ANS · Anvisa | MEC · Capes |
| Pasta no Drive | `<raiz>/Saúde` | `<raiz>/Educação` |

Google News, Valor (RSS) e Brazil Stock Guide rodam nas **duas**, sempre com as keywords/fontes da
vertical em questão. Sem palavras-chave, a vertical coleta só os portais gov.br.

## Mapa de arquivos (4 que importam)
| Arquivo | Responsabilidade | Mexa aqui quando… | Tam. |
|---|---|---|---|
| `clipping_core.py` | **Coleta** (Google News, portais gov.br, Valor RSS, Brazil Stock Guide) + decode de links + verticais | mudar fontes, keywords, portais, janela | ~640 ln |
| `clipping.py` | **Entrega**: e-mail + Google Drive + backlog + `main()` | mudar e-mail, Drive, destinatários | ~250 ln |
| `streamlit_app.py` | **Painel** (celular): rodar agora, agendar, debug | mudar a UI / o agendamento / os logs | ~210 ln |
| `.github/workflows/clipping.yml` | **Execução** no GitHub (workflow_dispatch) | mudar inputs, deps, env | ~35 ln |

Suporte: `clipping_requirements.txt` (deps), `SETUP_APP.md` (passo-a-passo de configuração), `clipping_contexto.md` (contexto de negócio).

## Fluxo (de ponta a ponta)
```
[App Streamlit / cron-job.org]  --workflow_dispatch (API)-->  [GitHub Action]
                                                                    |
                                                          python clipping.py
                                                                    |
                          clipping_core.collect(WHEN)  -->  DataFrame (8 colunas)
                                                                    |
                              build_email_html + XLSX  -->  e-mail (Gmail SMTP)
                                       sync_to_drive    -->  Google Drive + backlog
```
- **Coleta** (`clipping_core.collect`): roda cada fonte, junta, remove duplicatas (título e link),
  decodifica links do Google News para a URL real do veículo. Devolve as colunas:
  `title, count_news, link, source, date, hour, searched_keyword, source_link`.
- **Janela** (`WHEN`): "1h","6h","1d","3d"… é um corte por data/hora (`1d` = últimas 24h).

## Onde mexer pra cada coisa (cheat-sheet)
- **Adicionar/remover palavra-chave** → pelo app (aba Config) ou editando `keywords_<vertical>.txt`.
- **Aceitar nova fonte do Google News** → pelo app (aba Config) ou `sources_<vertical>.txt`.
- **Adicionar um portal gov.br a uma vertical** → `clipping_core.py`, dict `VERTICAIS`.
- **Adicionar uma fonte nova (RSS/scraper)** → `clipping_core.py`: escreva um `_scrape_xxx()` que
  devolva tuplas no formato `COLS`, e some o resultado em `collect()` (lista `frames`).
- **Mudar o visual/conteúdo do e-mail** → `clipping.py`, `build_email_html()`.
- **Mudar destinatários padrão** → `clipping.py`, `_DEFAULT_EMAIL_TO` (ou pelo app, sem mexer no código).
- **Mudar o que vai pro Drive / backlog** → `clipping.py`, `sync_to_drive()`.
- **Mudar o prompt da IA** → `clipping.py`, `AI_PROMPT`.
- **Mudar horário agendado** → pelo app (aba 🕗 Agendamento) — não precisa editar código.
- **Mudar a UI do app** → `streamlit_app.py`.

## Portais gov.br — à prova de mudança de endereço
`_scrape_govbr_auto()` tenta, nesta ordem: **1)** API REST na raiz do site (`++api++/@search`);
**2)** descoberta do link de notícias no menu da home; **3)** descoberta pela seção mais frequente
no `sitemap.xml`; **4)** caminhos conhecidos; **5)** coleta direta pelo `sitemap.xml`.
Só avisa alto no log se **nenhum** método achar a seção. (A ANS já trocou `/noticias` por
`/noticias-1` sem aviso e a antiga passou a redirecionar para login com HTTP 200.)
Dois templates de listagem são suportados: `.listagem-noticias-com-foto li` (ANS, Capes) e
`article.tileItem` (MEC — sem data na listagem, buscada na página do artigo).

## Onde ficam os secrets
- **GitHub → Settings → Secrets and variables → Actions** (usados pelo `clipping.py`):
  `EMAIL_REMETENTE`, `EMAIL_SENHA`, `GOOGLE_CREDENTIALS_JSON`, `DRIVE_FOLDER_ID`.
- **Streamlit Cloud → Settings → Secrets** (usados pelo app):
  `github_pat`, `cronjob_api_key`, `github_owner`, `github_repo`, `workflow_file`, `branch`, `default_recipients`.
- **Nada de senha/credencial fica no código** — tudo vem de env/secrets.

## Debug pelo celular
Aba **🔧 Debug** do app: checa conexões, lista as execuções e **mostra o log de erro do Action dentro do app**
(não precisa abrir o PC). Veja `SETUP_APP.md`.

## Custo: tudo grátis
- GitHub Actions (repo privado): 2.000 min/mês grátis no plano Free; cada run ~3-5 min (~150 min/mês). **Não precisa de GitHub Pro.**
- cron-job.org e Streamlit Community Cloud: grátis.

## Como pedir pro Claude editar (no plano Pro, com contexto menor)
> "Leia `ARCHITECTURE.md` e o `clipping_core.py`. Quero adicionar a fonte X." — isso basta; não precisa colar o repo inteiro.
