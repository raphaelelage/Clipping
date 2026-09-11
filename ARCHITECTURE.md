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
| Âncoras (p/ keywords com `+`) | `ancoras_saude.txt` | `ancoras_educacao.txt` |
| Fontes | `sources_saude.txt` | `sources_educacao.txt` |
| Prompt | `ai_prompt_saude.txt` | `ai_prompt_educacao.txt` |
| Portais gov.br | ANS · Anvisa | MEC · Capes |
| Pasta no Drive | `<raiz>/Saúde` | `<raiz>/Educação` |

Google News, Valor (RSS) e Brazil Stock Guide rodam nas **duas**, sempre com as keywords/fontes da
vertical em questão. Sem palavras-chave, a vertical coleta só os portais gov.br.

### Keywords ancoradas (`+termo`)
Termo genérico (`aquisicao`, `ICMS`, `medida provisória`) ou nome ambíguo (`Anhanguera` = rodovia,
`Pisa` = torre/futebol, `Raia` = natação) traz notícia de **qualquer setor** — medido na vertical
educação (4/set/2026): 123 de 638 itens do Google News eram MP da taxa das blusinhas, máfia do ICMS
e hospitais da Rede D'Or. Linha começando com `+` no `keywords_<vertical>.txt` vira **ancorada**:
só conta se a notícia também citar uma palavra de `ancoras_<vertical>.txt`. Como funciona
(`clipping_core._kw_termo` / `_gn_consulta` / `match_keywords`):
- Google News: a busca vira `termo (âncora1 OR âncora2 …)` — o Google casa no texto inteiro.
- Fontes **amplas** filtradas por keyword (feeds de economia `GRANDES_ECONOMIA`, Valor RSS, JOTA,
  CADE, Brazil Stock Guide): o **título** precisa ter termo + âncora (`ancorar=True`, padrão).
- Fontes **setoriais** (G1 Educação, Folha Saúde, entidades WP, scoop.it): `ancorar=False` — o
  feed já é a âncora, senão perderíamos "Países ricos têm pior desempenho no Pisa" (sem âncora
  no título). A lista de feeds amplos é `fontes_extra.FEEDS_AMPLOS`.
- A coluna `searched_keyword` e os logs mostram o termo **sem** o `+`.
- Combinada e seções custom: âncoras seguem a mesma herança das keywords (união das bases).
Sem `ancoras_<vertical>.txt`, valem os `DEFAULT_ANCORAS` do `clipping_core`.

**Aspas na keyword = frase exata no Google News** (opt-in, POR keyword). Solta, o Google casa
as palavras separadas: `Ser Educacional` trazia 60 itens/semana, 51 sem a empresa; `Arco
Educação` trazia quiz do Enem. **NUNCA torne isso automático**: frase exata não tolera acento
faltando — `autorizacao de curso` foi de 53 itens para **0**, `novo ensino medio` de 59 para 4,
`block trade` de 3 para 0 (medido em 8/set/2026, janela 14d). Regra prática: aspas só em nome
próprio digitado com a grafia real (`"Ser Educacional"`, `"Grupo Salta"`, `"Dr. Consulta"`);
termo temático/sem acento fica solto. Pode combinar com âncora: `+"Nome Ambíguo"`.

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
- **Keyword trazendo notícia de outro setor / nome ambíguo** → prefixe com `+` (ancorada) e, se
  precisar, ajuste `ancoras_<vertical>.txt`. Não crie filtro novo no código.
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
| WP | `<site>/wp-json/wp/v2/posts?after=<ISO>` | ANAHP, Interfarma, SindHosp, ABIMED, ABIIS, Cofen · Semesp, ANUP, Todos Pela Educação, Educa Insights |
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

**WAF vs IP do Actions**: ANAHP/ABIIS/Interfarma bloqueiam o IP de datacenter do
GitHub (403/404/202) mas respondem 200 do PC — o `_wp_json` tenta direto e cai no espelho
`r.jina.ai`, que repassa o MESMO JSON (medido 9/set/2026). O Substack (Valor & Saúde) também
bloqueia e NENHUM espelho repassa XML íntegro (jina renderiza; allorigins/corsproxy falham) —
no Actions essa falha é log esperado, não aviso; a **Abifina foi REMOVIDA** (10/09/2026) porque o Cloudflare dela exige CAPTCHA de IP de datacenter e bloqueia direto E espelho — para religar, devolver a linha em `WP_SITES`; do runner self-hosted a fonte volta sozinha.
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
Erro no radar nunca derruba o clipping (try/except com log `[radar]`) — por isso **olhe o log**:
`[radar] erro nao-fatal: 'curso'` ficou 5 rodadas (2-8/set/2026) sem ninguém notar. Causa: ato só
de instituição (credenciamento, sancionador) não tem tabela de cursos e o DataFrame nascia sem a
coluna `curso`; `dou_alerta.coletar_novidades` agora garante as colunas antes de filtrar.
Códigos de IES vêm de `cadastro_ies.parquet` (consolidado dos censos INEP 2018-2023).
ATENÇÃO: "\bMEDICINA\b" com borda de palavra — sem isso BIOMEDICINA conta como Medicina.

## Scoop.it (curadoria "Educação 3.0")
`fontes_extra._scoopit`: a pagina nao tem RSS nem filtro de data, mas cada card do HTML
ja traz o LINK ORIGINAL da noticia (o <a> do titulo — nunca devolva o link do scoop.it),
a data de curadoria e a data de publicacao no site original — por isso a coleta nao
visita noticia nenhuma. O criterio e a data de PUBLICACAO original >= janela; a varredura para quando a
CURADORIA sai da janela — como ninguem cura noticia antes de ela existir,
publicacao <= curadoria sempre, entao nao ha nada mais novo nas paginas seguintes. Filtra pelas
keywords da vertical (titulo + trecho do blockquote). Novas paginas: dict SCOOPIT.

## Summary de valuation no e-mail (`valuation.py`)
Tabela compacta no topo do e-mail (depois do botão de download) com a cobertura:
preço/alvo/retorno/mktcap/ADTV/P-E 26E/EV-EBITDA/DL-EBITDA/ROE/ROIC + Receita/EBITDA/Lucro
26E-27E. Cadeia de fontes POR CAMPO: snapshot Bloomberg (gerado no PC do usuário por
`valuation_bbg.py`, válido 5 dias) > Yahoo (yfinance) > cache do Drive (último valor bom).
Regras: nunca estimar número que a fonte não deu ("–"); P/E 26E só com consenso >=4
analistas e moeda coerente (ADR mistura USD/BRL — senão usa forwardPE); 1ª rodada do dia
consulta o Yahoo (~10s), as demais usam o cache diário. Empresas em
`empresas_valuation_<vertical>.txt` (editável no app). `bbg_snapshot.json` e
`valuation_cache.json` NUNCA vão ao repo (dados licenciados/derivados — .gitignore).

## Seções (verticais) dinâmicas — `verticais.json`
O app cria/renomeia/exclui seções. Cada seção nova herda a ESTRUTURA (portais gov.br,
DOU, CVM, RSS, scoop.it, radar) das bases saude/educacao via `HERANCAS`; keywords/fontes/
prompt/empresas são arquivos próprios `*_<chave>.txt` — vazios/ausentes caem na união da
herança (mesma regra da combinada). O input `vertical` do workflow aceita qualquer chave.

## Anti-duplicata de disparos
O cron-job.org às vezes reenvia o gatilho (retry) — chegavam 2 runs com segundos de
diferença e 2 e-mails. O job `escolher` se anula se existir run mais antigo (<5 min,
desempate por id). Precisa de `permissions: actions: read`.

## Funil de regulacao (funil.py)
Deriva da aba Atos o ESTADO ATUAL de cada curso (aba Funil, 1 linha/curso): trilho
autorizacao -> reconhecimento -> renovacao (fase = ato do trilho mais RECENTE, nao o "maior"
— universidades tem autonomia e podem estrear direto no reconhecimento); fase 0
(protocolado/sobrestado ADC 81) so existe para Medicina (planilhas SERES); vagas/cautelar/
sancionador/via judicial sao colunas, nao fases. ref_judicial: "nao consta na fonte" e "Nao
se aplica" significam SEM referencia (sem esse filtro, via_judicial marcava 100%). Medicina
com MEDICINA (BIOMEDICINA ja contaminou). Nunca edita Atos; so reescreve a aba Funil.

### status_regulatorio (Funil, 10/set/2026)
Coluna derivada, deterministica: pendente sobrestado -> "Travado (ADC 81)"; pendente com via
judicial -> "Vivo (Portaria 531/2023)"; pendente ordinario -> "Sem trilho (edital revogado,
Portaria MEC 129/2026)"; decidido com cod_curso nas Portarias SERES 72-76/2026 (cautelares do
Enamed, parseadas do DOU em cautelares_enamed_2026.json) -> "Restrito - Enamed (...)".
Nada e inferido por IA: so campos existentes + base oficial com URL da fonte no json.

### Funil automatico no robo (10/set/2026)
Sempre que o radar acrescenta ato novo, _radar_e_excel regenera a aba Funil (funil.py) e os
graficos (funil_graficos.py) ANTES do upload ao Drive — pipeline 100% deterministico, sem IA.
Abas derivadas (Funil/Graficos/Graf_Dados) NAO entram em abas_extra: reescreve-las como
dataframe mataria os graficos nativos e a nota de cabecalho. Falha no funil vira aviso no
e-mail e o upload segue so com Atos/Medicina. Rodada manual local: "Atualizar Funil DOU.bat"
na pasta do projeto.

### Radar com ESTADO (10/set/2026)
A ultima checagem do DOU fica gravada na aba Notas do Regulacao_Cursos.xlsx do Drive
(Assunto "radar_ultima_checagem") e e o PONTO DE PARTIDA da proxima varredura — nao ha mais
janela fixa. Dia com edicao inacessivel NAO avanca o estado (sera revarrido); minimo 3 dias
uteis, teto 30 (acima disso avisa e pede radar_dias). O e-mail mostra sempre o periodo
coberto ("edicoes de X a Y verificadas"). Regrava/sobe o arquivo so quando ha ato novo,
Funil ausente ou estado avancado — rodadas duplas no mesmo dia nao re-sobem nada.

### Resumos: download em threads, PARSING em subprocesso (10/set/2026)
O exit 134 ("corrupted size vs. prev_size", core dumped) que derrubou a rodada vinha do
parser NATIVO (trafilatura/lxml) chamado por 12 THREADS ao mesmo tempo sobre HTML malformado.
try/except NAO segura abort de biblioteca C. Agora: download continua em threads (so rede) e
o parsing roda SEQUENCIAL num ProcessPoolExecutor de 1 worker — worker que morre perde SO
aquele resumo, o pool e recriado e a rodada segue (vira aviso no e-mail). Se o subprocesso
nao subir no ambiente (sonda no inicio), cai para parsing sequencial no proprio processo —
nunca fica sem saida. Teto de 90s alem do budget de download.

### Janela: RSS com data em PORTUGUES (10/set/2026)
Feeds brasileiros publicam "Qui, 10 Set 2026 14:16:31 -0300" (UOL) e "Qui, 10/09/2026 - 12:05"
(Fiocruz). NEM o feedparser NEM o parse RFC822 entendem -> o item ficava SEM data e ESCAPAVA
do corte de janela: 87 itens/rodada (72 so da Fiocruz, 70 deles VELHOS). `_data_entrada` agora
tenta em 3 camadas: *_parsed do feedparser -> RFC822/ISO -> portugues (`_data_pt`: traduz
dia/mes e entende dd/mm/aaaa - HH:MM). Item que mesmo assim nao tiver data e DESCARTADO
(idade desconhecida nunca entra) e a contagem sai no log. Medido depois: 0 sem data.

### Correcao da base do DOU (10/set/2026) — 3 bugs de extracao
Descobertos ao investigar "por que so ate 2021?" no grafico G8. A base dizia ZERO autorizacao
de Medicina em 2022-2026; o certo sao 147 (66 so em 2024, do Edital 1/2018 concluindo).
1. INDEFERIMENTO contado como AUTORIZACAO: "Indeferir o pedido de AUTORIZACAO do curso"
   casava com a regra de autorizacao — REJEICAO virava aprovacao (1.073 atos).
2. "Fica autorizado/reconhecido o curso" nao casava com autoriza(cao|r|m)?: o ato era
   classificado pelos considerandos (2.582 autorizacoes eram, na verdade, renovacoes).
3. Portaria de curso UNICO poe curso/vagas/IES/mantenedora/municipio na PROSA do Art. 1,
   e o extrator so lia TABELAS — esses atos entravam vazios.
Correcao: `classificar` le o VERBO no inicio do Art. 1 (_DISPOSITIVOS) e so sobrescreve
quando o verbo e inequivoco — fora disso vale a regra classica (zero regressao, validado
por amostragem do texto real). `detalhes_da_prosa` extrai os campos do Art. 1.
Retroativo SEM refazer coleta: `reprocessar_dou.py` (usa o texto ja salvo no parquet) e
`corrigir_base_dou.py` (aplica na planilha, so em celula vazia e so em ato de 1 linha).
Novo tipo `indeferimento`: entra em ALARME_SEMPRE (rejeicao e material) e vira a fase
"F. Indeferido" no Funil + o grafico G10 (autorizados x indeferidos por ano).

### Auditoria v3 da base do DOU (10/set/2026) — verbos restantes + dedup
Varredura de TODOS os verbos decisorios do Art. 1 nos 58k atos. Novos tipos/dispositivos:
extintos->desativacao (3.8k linhas sairam de reconhecimento; as tabelas de extincao NAO
trazem nome/codigo do curso, so processo e-MEC novo + IES — por isso ficam FORA do funil
por curso, sem vinculo inventado), revogacao, sem_efeito (ambos alarmam no radar),
unificacao_mantidas (206 linhas-curso que inflavam autorizacao; NAO alarma),
suspensao de chamada publica -> chamamento_mais_medicos.
DEDUP da 1a carga colapsava (ato+processo+curso+ies) sem municipio/vagas: 1.997 linhas
legitimas sumiram (mesmo curso em municipios distintos). Chave corrigida em dou_montar e
linhas devolvidas pela correcao v3 (suplemento com teto por trio link+curso+ies — nunca
adiciona alem do deficit; processo numerico vira texto sem .0 na chave).
Marca na aba Notas: correcao_v3_aplicada (roda 1x no robo, idempotente).

### Situacao do curso pelo Cadastro e-MEC (10/set/2026)
situacao_cursos_emec.parquet (627 KB, 86.239 cursos) sai do CSV publico "Cursos de Graduacao
do Brasil" (dados abertos do MEC, 225 MB — baixado a mao pelo dono porque o portal exige
CAPTCHA; o CSV cru NAO entra no repo). E a UNICA fonte que diz se o curso ainda existe:
o DOU publica a extincao sem nomear o curso (so processo + IES) e o Censo INEP so enxerga
curso em atividade. Coluna situacao_emec no Funil (Em atividade / Em extincao / Extinto),
pintada de AMARELO e declarada na nota de cabecalho, como todo dado de fonte externa.
Casamento por cod_curso: 20.666 dos 21.929 cursos com codigo (94%).
Para atualizar: baixar o CSV de novo (1-2x/ano) e regerar o parquet.

### v4 (11/set/2026): a decisao pode estar no Art. 2
Portaria cujo Art. 1 e PROCEDIMENTAL ("Anular a Portaria X", "Revogar a Portaria Y") poe a
decisao real no Art. 2 — "Art. 2o Indeferir o pedido de autorizacao do curso de Medicina".
Lendo so o Art. 1, o ato caia em autorizacao: 3 INDEFERIMENTOS DE MEDICINA (Portarias SERES
281, 371 e 454/2026) estavam registrados como APROVACAO. `classificar` agora consulta o
Art. 2 quando o Art. 1 e procedimental; revogacao pura (sem decisao no Art. 2) segue
revogacao. Achado ao responder "quais checks provam que o total do scraper esta correto".
COBERTURA: os 770 dias uteis sem ato NAO sao falha de coleta — amostra de 20 dias checada
na fonte viva: 19 tem ato do MEC, mas ZERO tem ato de regulacao de curso (nomeacao,
exoneracao etc. sao corretamente filtrados por `relevante()`).

### v5 (11/set/2026): varredura final + enriquecimento
CLASSIFICADOR — 3 verbos que faltavam, achados varrendo TODO dispositivo da base:
"Fica DESATIVADO o curso" caia em reconhecimento (o Art. 2 renova o reconhecimento so
para emissao de diploma, mas o curso esta FECHANDO) — 22 docs; "extinguir," com virgula
escapava de "extinguir\s" — 3 atos / 372 linhas; "reduzir de X para Y vagas" e "aplicar
medida cautelar" idem. Regressao de 23 casos no teste.
ENRIQUECER (enriquecer.py) — duas camadas: (1) codigo que JA estava no ato ("UFMG(575)",
campo numerico) = 1.092 cod_ies, NAO pinta (a fonte segue sendo o DOU); (2) cruzamento
e-MEC + cadastro INEP por nome (variantes seguras, sempre IGUALDADE exata) = 1.471 cod_ies,
2.428 cod_curso, 221 municipio, 225 uf, 38 vagas — PINTA de amarelo.
VALIDACAO: cod_ies preenchido por mim diverge do nome oficial em 0,8
### v5 (11/set/2026): varredura final + enriquecimento
CLASSIFICADOR — 3 verbos que faltavam, achados varrendo TODO dispositivo da base:
"Fica DESATIVADO o curso" caia em reconhecimento (o Art. 2 renova o reconhecimento so para
emissao de diploma, mas o curso esta FECHANDO) — 22 docs; "extinguir," com virgula escapava
do padrao antigo — 3 atos / 372 linhas; "reduzir de X para Y vagas" e "aplicar medida
cautelar" idem. Regressao de 23 casos no teste.
ENRIQUECER (enriquecer.py) — duas camadas: (1) codigo que JA estava no ato ("UFMG(575)",
campo numerico) = 1.092 cod_ies, NAO pinta (a fonte segue sendo o DOU); (2) cruzamento
e-MEC + cadastro INEP por nome (variantes seguras, sempre IGUALDADE exata) = 1.471 cod_ies,
2.428 cod_curso, 221 municipio, 225 uf, 38 vagas — PINTA de amarelo.
VALIDACAO: cod_ies preenchido pelo cruzamento diverge do nome oficial em 0,8%; os que JA
vinham do DOU divergem em 6,2% (IES renomeadas: Anhanguera, Uninassau, Afya, Estacio). As
duas fontes independentes (e-MEC x cadastro INEP) concordam em 2.890 nomes, discordam em 6.
COLUNAS NOVAS: link_fonte (ultima, 100% preenchida) leva ao ato no DOU; fonte_inep virou
fonte_externa (carrega INEP e e-MEC). Datas gravadas sem horario em todas as abas.
situacao_emec: coluna INTEIRA e externa, entao o CABECALHO e amarelo (nao as 23 mil celulas).

### Varredura completa por periodo (11/set/2026) — dou_varredura.py
Pipeline INTEIRO do zero, sem IA, para um intervalo [inicio, fim]: coleta dia a dia
(dias uteis = do1 + do1_extra; fds/feriado = so extra), retentativa de dia falho e
FALHA NOMINAL no relatorio se persistir (garantia: nenhuma decisao passa em silencio);
extracao completa (dx.extrair: texto integral, tabelas, prosa, classificador v5);
fusao no Excel pela chave completa link+processo+curso+ies+municipio+vagas (nao
duplica nem sobrescreve enriquecimento); cobertura registrada na aba Notas
("varredura_cobertura"); estado do radar adiantado quando a varredura chega em hoje
sem falha; Funil+graficos regenerados; relatorio por e-mail ([INCOMPLETO] se houver
dia nao coberto). MEDIDO: ~2,6s/dia util + ~4 min de Funil — 1 mes ~5 min, 1 ano
~15 min, 2018-hoje ~105 min (cabe no PC e no limite de 6h do Actions).
Camadas: varredura_ci.py (Drive download/upload + e-mail, usado pelo workflow
varredura.yml — PC-primeiro via RUNNER_PAT, concurrency para nao rodarem duas juntas)
e aba "Varredura DOU" no streamlit_app.py (datas com fim=hoje, e-mail, rodar agora +
agendamento cron-job.org com dias_retro). No PC: "Varredura DOU.bat" (arquivo local).

### Sentinelas S2/S3 + detector Enamed (11/set/2026, aprovados pelo dono)
O radar diario, alem de do1+do1_extra, agora VIGIA (camada de alerta, sem afetar o
funil nem o estado): SECAO 3 — "chamamento publico/edital" + "medicina" (novo edital e
resultados saem la; hoje nao ha edital vivo); SECAO 2 — nomear/exonerar/designar no
comando da SERES ou presidencia do INEP (sinal regulatorio). Sentinela vira linha na
aba Atos (tipo sentinela_*, dedup por link) e frase destacada no e-mail. Falha de
do2/do3 so gera log — NUNCA trava o avanco do estado (garantia continua em do1/extra).
ENAMED: ato citando Enamed com numero de portaria FORA do cautelares_enamed_2026.json
vira aviso no e-mail pedindo regeneracao do JSON (nada de parse automatico de anexo).

### Manutencao das bases estaticas — TUDO por script, sem IA (11/set/2026)
Vulnerabilidade fechada a pedido do dono (migracao Max->Pro): as bases estaticas eram
construidas em sessao de IA; agora cada uma tem script commitado e VALIDADO
(reconstrucao comparada valor a valor com a base original):

| base                        | script                    | gatilho                | passo manual |
|-----------------------------|---------------------------|------------------------|--------------|
| cautelares_enamed_2026.json | atualizar_cautelares.py   | aviso ENAMED no e-mail | colar os links das portarias |
| cursos_emec/ies_emec        | atualizar_emec.py         | 1-2x/ano               | baixar CSV no navegador (CAPTCHA) |
| cursos_inep.parquet         | atualizar_inep.py         | novo Censo (anual)     | baixar zip de microdados |

CORRECAO de bonus achada na validacao do INEP: o parquet da 1a geracao gravava o
PRIMEIRO polo (ordem do arquivo!) como municipio de curso EAD — polo arbitrario. A
regra agora e a mesma do e-MEC: municipio so quando inequivoco; EAD multi-polo = vazio.
O que AINDA exigiria sessao de IA: (1) mudanca de formato na API leiturajornal do
in.gov.br (quebra RUIDOSA: aviso + job vermelho, nunca silenciosa); (2) nova planilha
de pendentes da SERES (PDF -> tabela, parada em 06/2024); (3) evolucao de features.
Rotina diaria e varredura: ZERO tokens de IA por construcao.

### Atualizacao da planilha SERES: prompt pronto + cirurgia por script (11/set/2026)
Quando sair foto nova dos pendentes de Medicina: abrir PROMPT_ATUALIZAR_SERES.md na
raiz do repo e colar o prompt numa sessao do Claude. A sessao so faz o PDF->CSV
(conferindo totais contra o PDF); a troca na base e o atualizar_seres.py, testado:
remove os pendentes da foto antiga, insere os novos com os tipos EXATOS de
funil.FASE_PENDENTE e reconstroi a aba Medicina_SERES. Depois: regenerar funil e
atualizar a data em ST_SOBRESTADO. A data que vale e a de DENTRO do PDF (a da pagina
do MEC ja enganou: dizia 04/2025 com PDF de 07/06/2024).

### v7 (11/set/2026, aprovacoes do dono)
- ESTADUAIS/MUNICIPAIS na base: cursos de IES publicas estaduais/municipais entram como
  linha INTEGRAL do e-MEC (fase "(sistema estadual/municipal)", via "Sistema estadual"),
  porque a regulacao deles e dos Conselhos Estaduais (diario do estado, nao DOU) —
  ~6.1k cursos, 58 Medicinas ativas (UERJ, UPE...). cursos_emec.parquet ganhou a coluna
  `categoria`. No clipping, noticias dessas IES chegam pelo Google News; monitorar 27
  diarios estaduais nao e viavel gratis/deterministico (registrado e comunicado).
- municipio_check REMOVIDA do Funil (padronizacao IBGE continua, so o carimbo saiu).
- Aba MEDICINA REMOVIDA (era vista filtrada de Atos): nenhum escritor grava mais
  (clipping/corrigir/varredura/atualizar_seres/dou_montar) e o funil.gerar a DESCARTA de
  arquivos antigos. Medicina_SERES FICA: e insumo (regime_seres) e prova da foto oficial.
- COMO_ATUALIZAR.md: runbook completo de operacao sem IA (dia a dia, varredura, Ajustes,
  manutencao das bases, investigacao, desastres).
