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
| `coletar_shard.py` | **Um robô da coleta dividida** — busca só a sua fatia | mexer no fatiamento | ~50 ln |
| `benchmark_gn.py` | Compara regimes de busca no IP do Actions | validar mudança na busca | ~120 ln |
| `fontes_extra.py` | **Fontes complementares** (entidades WP, RSS próprios, DOU, CVM) | adicionar/remover fonte extra | ~240 ln |
| `streamlit_app.py` | **Painel** (celular): rodar agora, config, agendar, debug | mudar a UI / o agendamento / os logs | ~330 ln |
| `.github/workflows/clipping.yml` | **Execução** no GitHub (workflow_dispatch) | mudar inputs, deps, env | ~35 ln |

Suporte: `clipping_requirements.txt` (deps), `SETUP_APP.md` (passo-a-passo de configuração), `clipping_contexto.md` (contexto de negócio).

## Fluxo (de ponta a ponta)
```
[App Streamlit / cron-job.org]  --workflow_dispatch (API)-->  [GitHub Action]
                                                                    |
        ┌───────────────── job "coletar" (matriz de 4, em paralelo) ─────────────────┐
        │  robô 1: keywords 1,5,9…    robô 3: keywords 3,7,11…                       │
        │  robô 2: keywords 2,6,10…   robô 4: keywords 4,8,12…                       │
        │  cada um: coletar_shard.py -> gn_shard_N.csv (artifact)                    │
        └────────────────────────────────────────────────────────────────────────────┘
                                                                    |
                                              job "montar": python clipping.py
                                                                    |
                          clipping_core.collect(WHEN)  -->  DataFrame (9 colunas)
                                    (Google News já pronto, vindo das 4 fatias)
                                                                    |
                              build_email_html + XLSX  -->  e-mail (Gmail SMTP)
                                       sync_to_drive    -->  Google Drive + backlog
```

### Por que a coleta é dividida em 4 robôs
Os 153s do Google News **não eram rede**: eram as pausas de 0,5s entre as 122 buscas,
obrigatórias para não tomar bloqueio. Threads dentro de um processo só **já foram testadas
e falharam feio** — a coleta caiu de ~225 para 12 notícias, porque o Google limita por IP e
a rajada saía toda do mesmo lugar. Jobs separados resolvem porque o Actions põe cada um numa
máquina com IP próprio (medido numa mesma execução: `20.119.x`, `20.168.x`, `135.232.x`,
`172.182.x`). Cada robô mantém o mesmo ritmo seguro de 0,5s, só que sobre um quarto das
keywords. **Medido: 153s → ~30s, com cobertura idêntica** (3549 itens, as mesmas 13 vazias).

A fatia é **intercalada** (`keywords[shard-1::shards]`), não em blocos: assim toda fatia
recebe uma mistura de keywords produtivas e raras e todas terminam em tempo parecido.

**Se um robô cair** (`_juntar_shards()`, em `clipping.py`), há dois degraus antes de desistir:
1. **Refaz a fatia ali mesmo.** O job de montagem já tem a mesma lista de keywords e o mesmo
   código de busca, então recoletar só aquela fatia custa ~30-45s e é mais simples que
   reexecutar o job. Medido: robô 3 derrubado → 125 itens recuperados em 44s, sem aviso.
2. **Se a segunda tentativa também falhar**, o clipping vai assim mesmo — mas gritando:
   `[INCOMPLETO]` no assunto e uma faixa vermelha no topo do e-mail listando exatamente
   quais palavras-chave ficaram de fora.

O que não pode acontecer é clipping incompleto com cara de completo: essa é a perda invisível.

**Reverter para o modo sequencial:** `GN_SHARDS: "0"` no topo do `clipping.yml`. Nada mais muda —
`collect()` volta a buscar sozinho.

### Thread pode aqui, não podia no Google News
Dentro de `collect()`, as fontes restantes (4 portais gov.br + Valor + Brazil Stock Guide +
`fontes_extra`) rodam **todas ao mesmo tempo** num `ThreadPoolExecutor`. Isso parece contradizer
a regra "não usar thread no Google News", mas a diferença é qual servidor apanha: o desastre do
Google News foi **rajada contra um host só**, que limita por IP. Aqui cada tarefa fala com um
servidor diferente. A única concentração é o `gov.br`, que atende 4 portais — medido em série
× simultâneo: **38,1s → 29,3s** com as mesmas contagens e nenhuma recusa.
Se um dia o gov.br começar a recusar, limite **só ele** (menos workers ou os 4 portais de volta
em fila) — não serialize tudo de novo.
Medido no conjunto: esse bloco caiu de ~133s para ~31s.

### Onde o tempo está hoje (rodada de 3min04)
| Etapa | Tempo |
|---|---|
| 4 robôs do Google News (em paralelo) | ~60s (dos quais ~30s é coleta, resto é setup) |
| Fontes restantes (simultâneas) | ~31s — dominado pelo MEC, que sozinho leva ~35s |
| Extrair parágrafos de cada notícia | ~44s |
| Decodificar links + e-mail + Drive | ~40s |

O próximo alvo, se algum dia precisar, é o **MEC**: o template dele não traz data na listagem,
então é preciso abrir cada artigo para descobrir a data — e isso ainda é feito em fila.
- **Coleta** (`clipping_core.collect`): roda cada fonte, junta, remove duplicatas (título e link),
  decodifica links do Google News para a URL real do veículo, junta a **mesma notícia publicada
  por veículos diferentes** (`_dedup_similar`) e baixa os 3 primeiros parágrafos de cada notícia
  (`_extrair_resumos` → coluna `resumo`, que vira o `TRECHO:` no texto para a IA). Colunas:
  `title, count_news, link, source, date, hour, searched_keyword, source_link, resumo`.

### `_dedup_similar` — cuidado ao mexer
Usa `rapidfuzz.token_set_ratio` com **limiar 85**, escolhido auditando o corpus real de 906 títulos:
85 → 39 fusões, 38 corretas; 72 → 95 fusões, várias **erradas**. Tem duas travas que valem em
qualquer limiar: não funde títulos que citam **empresas cobertas diferentes** nem **trimestres
diferentes** (sem elas, "Hapvida tem lucro no 2T26" × "Cogna tem lucro no 2T26" pontua 78 — mais
que duas versões da mesma notícia da Hapvida, que pontuam 72). Perder uma duplicata é barato;
perder um fato relevante de empresa coberta não é. Não baixe o limiar sem refazer a auditoria.
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
- **Mudar quantos robôs coletam em paralelo** → `GN_SHARDS` e a `matrix.shard` no
  `clipping.yml` (os dois têm que bater). `0` volta ao modo sequencial.
- **Testar se uma mudança na busca perde notícia** → `benchmark.yml` compara dois regimes na
  mesma rodada, por conjunto de links de cada keyword. Nunca compare só a contagem: duas
  coletas separadas por minutos sempre diferem (medido: 51 perdidos × 54 ganhos = rotação
  normal, não regressão).

## Portais gov.br — à prova de mudança de endereço
`_scrape_govbr_auto()` tenta, nesta ordem: **1)** API REST na raiz do site (`++api++/@search`);
**2)** descoberta do link de notícias no menu da home; **3)** descoberta pela seção mais frequente
no `sitemap.xml`; **4)** caminhos conhecidos; **5)** coleta direta pelo `sitemap.xml`.
Só avisa alto no log se **nenhum** método achar a seção. (A ANS já trocou `/noticias` por
`/noticias-1` sem aviso e a antiga passou a redirecionar para login com HTTP 200.)
Dois templates de listagem são suportados: `.listagem-noticias-com-foto li` (ANS, Capes) e
`article.tileItem` (MEC — sem data na listagem, buscada na página do artigo).

## Fontes complementares (`fontes_extra.py`)
Rodam **todas em paralelo** (~4s no total, para não pesar no Actions). Cinco grupos:
| Grupo | Como | Exemplos |
|---|---|---|
| WP | `<site>/wp-json/wp/v2/posts?after=<ISO>` | ANAHP, Interfarma, SindHosp, ABIMED, Abifina, ABIIS, Cofen · Semesp, ANUP, Todos Pela Educação, Educa Insights |
| RSS setorial | feed próprio | Medicina S/A, Setor Saúde, Fiocruz, JOTA, CADE, Consumidor Moderno, INEP |
| RSS grandes | feed oficial do veículo, **com** filtro de keyword | G1, O Globo, Folha, Estadão, UOL, Agência Brasil, Jornal da USP (`GRANDES_ECONOMIA` + feeds de saúde/educação) |
| DOU | `in.gov.br` busca por **frase exata**, seções DO1 + DO1E (extra) | portarias do MEC, decisões da ANS, registros da Anvisa |
| CVM/SEC | **RAD** (tempo real) com o zip do IPE de reserva; SEC EDGAR para ADR | fato relevante / comunicado das cobertas · Afya (NASDAQ, CIK 0001771007) |

**Três armadilhas já medidas nesta parte** (não desfaça sem testar):
1. A SEC devolve **HTTP 403** se o `User-Agent` não tiver e-mail de contato. Como o repo é
   público, o e-mail vem do ambiente (`SEC_CONTATO`, senão `EMAIL_REMETENTE`) — nunca do código.
   Sem nenhum dos dois a SEC é pulada com aviso no log, sem quebrar a coleta.
2. O nome da empresa casa por **palavra inteira** (`_rx_empresas`). Substring simples fazia
   "ARCO" (Arco Educação) casar com **MARCOPOLO** e **ARCOS DORADOS** (McDonald's).
3. Uma janela de "1d" pede a **semana** à CVM e filtra por data localmente. Pedir "hoje"
   perdia o fato relevante publicado ontem à noite — que é justamente o que a rodada das
   06h45 precisa pegar (medido: 0 documentos com "hoje" × 5 com "semana").

**Por que o RAD e não só o zip do IPE:** o zip de dados abertos só consolida o documento no dia
seguinte — um fato relevante da manhã não sairia no clipping do mesmo dia. `_cvm_rad()` chama o
endpoint que o próprio site da CVM usa (sem captcha), descarta documento com status "Cancelado",
e devolve `None` se falhar — aí `_cvm_com_reserva()` cai automaticamente no zip. Como o endpoint
não é documentado, **a reserva é obrigatória**: nunca remova o fallback.

Cada fonte diz se aplica filtro de keyword: entidades do setor entram inteiras (`filtrar=False`);
fontes amplas (JOTA, CADE, DOU) filtram por palavra-chave. Links de arquivo (`.pdf`, `.jpg`) são
descartados e os RSS do gov.br exigem `/noticias/` no link — eles misturam documento com notícia.

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

## Levantamento histórico de regulação (ferramentas avulsas, não rodam no clipping diário)
Pedido: todos os atos que autorizam/barram cursos superiores desde 2018, foco em Medicina.
- `dou_historico.py` — varre a **edição diária** do DOU (`leiturajornal?data=...&secao=do1`) e
  guarda todos os atos cujo órgão começa com "Ministério da Educação". Não usa busca por frase
  de propósito: portaria com redação diferente escaparia. O endpoint falha de forma intermitente
  (200 sem o bloco JSON `{"typeNormDay"...}`) — há retry, e dia que falhar fica listado.
- `dou_extrair.py` — 1 linha por curso: explode as tabelas dos atos (a coluna de processo se
  chama "Registro e-MEC nº"; município/UF sai do endereço de funcionamento), classifica o tipo
  de ato e marca referência judicial.
- `medicina_mec_pdfs.py` — planilhas oficiais da SERES (tramitação + sobrestados ADC 81).
  O caminho no site é `assuntos/es/cursos-de-medicina/...` (o antigo `areas-de-atuacao/...` dá 404).
- `dou_montar.py` — junta tudo no Excel de 4 abas (Atos, Medicina, Medicina_SERES, Notas).

## Radar DOU (alerta de regulação de cursos no e-mail + Excel no Drive)
Nas verticais educação e saúde_educação, cada rodada lê a **edição diária** do DOU dos
últimos 3 dias úteis (`dou_alerta.py`, reusando `dou_historico`/`dou_extrair`), classifica
os atos do MEC e, para os alarmantes (autorização, vagas, credenciamento, cautelar,
sancionador — e reconhecimento/renovação só para Medicina), põe **uma frase por documento
no topo do e-mail** e anexa as linhas ao **`Regulacao_Cursos.xlsx` na pasta do Drive**
(mesmo formato do levantamento 2018-2026; semente versionada em `seed_regulacao_cursos.xlsx`).
Só alerta documento com linha inédita no Excel — rodadas seguidas não repetem o alarme.
Erro no radar nunca derruba o clipping (try/except com log `[radar]`).
Códigos de IES vêm de `cadastro_ies.parquet` (consolidado dos censos INEP 2018-2023).
ATENÇÃO: "\bMEDICINA\b" com borda de palavra — sem isso BIOMEDICINA conta como Medicina.

## Scoop.it (curadoria "Educação 3.0")
`fontes_extra._scoopit`: a pagina nao tem RSS nem filtro de data, mas cada card do HTML
ja traz o LINK ORIGINAL da noticia (o <a> do titulo — nunca devolva o link do scoop.it),
a data de curadoria e a data de publicacao no site original — por isso a coleta nao
visita noticia nenhuma. Varre paginas ate a curadoria passar de 2x a janela e o criterio
final e a data de PUBLICACAO original >= janela (regra pedida pelo usuario). Filtra pelas
keywords da vertical (titulo + trecho do blockquote). Novas paginas: dict SCOOPIT.
