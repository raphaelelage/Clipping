# COMO ATUALIZAR TUDO POR CONTA PRÓPRIA (sem IA no caminho)

Runbook do dono. Cada item diz: **quando**, **o que fazer**, **como conferir**.

---

## 0. O dia a dia — nada a fazer

O robô roda sozinho (cron-job.org → GitHub Actions, 09:40). Ele:
retoma da última checagem gravada na planilha → varre DOU Seção 1 + edição extra +
sentinelas (S2 comando / S3 chamamento) → classifica pelo dispositivo → funde no
`Regulacao_Cursos.xlsx` do Drive → regenera Funil + gráficos → e-mail.

**O e-mail é o painel.** Leia os sinais:
| Sinal no e-mail | Significado | Ação |
|---|---|---|
| Bloco "Radar DOU" | atos novos relevantes | ler; nada a operar |
| `SENTINELA S3 — CHAMAMENTO` | edital/resultado de medicina na Seção 3 | notícia grande; a base segue sozinha |
| `SENTINELA S2 — comando` | troca no MEC/SERES/INEP/ANS/ANVISA | notícia; nada a operar |
| `[AVISO] ENAMED: portaria nova...` | cautelares novas fora do JSON | rodar `python atualizar_cautelares.py <links do aviso>` e commitar |
| `[INCOMPLETO]` no assunto | dia do DOU inacessível | nada: o estado não avança e o dia é revarrido sozinho |
| linha "edições de X a Y verificadas" | período coberto na rodada | conferência visual |
| (sempre que abrir a planilha) | aba **Conferir** lista o que precisa de olho humano | ver §2b |

---

## 1. Varredura por período (backfill / reconstrução)

**Quando:** suspeita de buraco, ou quer refazer um intervalo.
**Onde:** aba **"Varredura DOU"** do app Streamlit (datas + e-mail + Rodar agora /
agendar) · ou no PC: `Varredura DOU.bat` · ou GitHub → Actions → `varredura-dou`.
**Custo:** ~2,6s/dia útil (+ ~4 min de Funil). 1 mês ≈ 5 min · 2018–hoje ≈ 105 min.
**Garantia:** dia que falhar sai NOMINALMENTE no e-mail; rode de novo o intervalo.
A cobertura fica gravada na aba **Notas** (`varredura_cobertura`).

## 2. Correção manual de dados (cod_ies, município, vagas...)

**NUNCA edite o Funil direto** — ele é regenerado a cada rodada e a edição evapora.
A correção vai na aba **Ajustes**, uma linha por correção:

| coluna | o que preencher |
|---|---|
| `cod_curso` | **prefira este** — copie da coluna cod_curso do Funil |
| `link` | alternativa, quando a linha não tem código: copie de link_fonte |
| `curso` | opcional, só para desambiguar ato com vários cursos |
| `campo` | cod_ies, cod_curso, curso, curso_padrao, ies, mantenedora, uf, municipio, vagas |
| `valor` | o valor correto |

Ela é reaplicada em **toda** regeneração, antes de qualquer cruzamento automático, e a
célula fica **VERDE** para sempre. Prefira `cod_curso`: o link do ato muda quando sai
ato novo para o curso (a fase passa a apontar para outro ato) e o ajuste por link
deixa de casar.

**Ajuste que não entrar você fica sabendo**: ele aparece na aba **Conferir** com o
motivo (chave não encontrada, campo inválido, valor em branco). Nada some em silêncio.

Onde pesquisar o valor certo: o próprio ato (link_fonte), o CSV do e-MEC na pasta
(Ctrl+F/PROCV) ou emec.mec.gov.br.

**Linha de ato que faltou:** dá para acrescentar à mão na aba **Atos** — ela é um log,
o robô só anexa, nunca reconstrói. A única coluna que ele reescreve é `tipo_decisao`
(vem do classificador, pelo link).

## 2b. A aba Conferir — o que o robô NÃO decide sozinho

Gerada a cada rodada a partir do Funil. Uma linha por pendência, com o problema
explicado em português e o que fazer. Nada nela foi alterado na base.

| problema | o que significa |
|---|---|
| Município divergente entre os atos | atos do mesmo cod_curso citam cidades diferentes: erro de digitação no DOU, mudança de campus, ou código trocado juntando dois cursos |
| Curso divergente entre os atos | mesmo código com nomes de curso incompatíveis — algum ato veio com código errado |
| DOU encerrou, e-MEC diz que existe | as duas fontes oficiais discordam (recurso deferido depois, ou cadastro desatualizado) |
| Curso aparece dos dois lados (estadual + DOU) | IES estadual/municipal com ato no DOU: o curso conta em dobro |
| Ajuste manual não aplicado | sua correção na aba Ajustes não casou com nenhuma linha |

Resolveu? Registre na aba **Ajustes**. A pendência sai da lista sozinha quando a causa
deixar de existir.

## 3. Manutenção periódica (bases de cruzamento)

| Quando | Comando (na pasta `C:\Users\Raphael\Dev\Clipping`) |
|---|---|
| E-mail avisar ENAMED novo | `python atualizar_cautelares.py <link1> <link2>` |
| 1-2×/ano (e-MEC) | baixar o CSV¹ → `python atualizar_emec.py` |
| Censo novo do INEP (anual) | baixar o zip de microdados → `python atualizar_inep.py <zip>` |
| SERES publicar planilha nova | abrir **PROMPT_ATUALIZAR_SERES.md** e colar numa sessão do Claude |
| Conferir as listadas (quando quiser) | `python conferir_listadas.py <arquivo.xlsx>` |

Depois de qualquer um: `git add *.parquet *.json && git commit -m "atualiza base X" && git push`
— o robô usa os arquivos do repositório.

¹ CSV "Cursos de Graduação do Brasil": https://dadosabertos.mec.gov.br/indicadores-sobre-ensino-superior/item/183-cursos-de-graduacao-do-brasil (o portal tem CAPTCHA — baixe no navegador e salve na pasta `Clipping New`).

## 3b. As duas abas de conferência

**Conferir** — sai de graça em toda regeneração, a partir do Funil. Uma linha por
pendência que o robô não resolve sozinho: município ou curso divergindo entre os atos de
um mesmo código, código de curso trocado, DOU encerrando um curso que o e-MEC diz ativo,
e ajuste da aba Ajustes que não casou. Ver §2b.

**Conferir - Listadas** — só sai quando você roda `python conferir_listadas.py`, porque
depende de baixar os fatos relevantes e comunicados ao mercado da CVM (dataset IPE,
público). Cruza o que YDUQS, Cogna, Ser, Cruzeiro do Sul, Ânima e Vitru comunicaram sobre
vagas/autorização de Medicina com os atos que a base tem, pelo **número da portaria** que
o próprio comunicado cita. Dois blocos: comunicado × base, e cursos de Medicina ativos no
e-MEC sem nenhum ato na base. A coluna **ajuste_no_funil** diz o que fazer em cada linha:
*VARREDURA* quando falta o ato (a linha do Funil nasce com ele) ou *ABA AJUSTES* quando a
linha existe e o valor é que está errado.

O grupo de cada IES vem de `grupo_ies.csv` (código da IES → grupo), no repositório. Esse
arquivo é usado **somente** neste cruzamento; nada no Funil depende dele. Quando comprar
ou vender faculdade, edite o CSV e faça commit.

## 3c. Backlog de nomes de curso

O `curso_padrao` é padronizado contra o catálogo de nomes do e-MEC: quando a linha tem
código de curso, usa o nome oficial dele; quando não tem, corta o rabo de localização
("no município de X", "do campus Y") e só aceita se o resultado existir no catálogo.

O que não resolve por código vai para **`curso_padrao_pendentes.csv`**, salvo ao lado da
planilha, com cada nome e quantas linhas ele afeta. São nomes que o próprio DOU escreveu
errado ou truncou. Para corrigir um deles: aba **Ajustes**, campo `curso_padrao`.

## 3d. Ordem das abas

Atos · Funil · Conferir · Conferir - Listadas · Ajustes · Graf_Dados · Gráficos ·
Medicina_SERES · Notas. É aplicada a cada regeneração; aba nova que apareça vai para o
fim, nunca some.

## 4. Onde investigar quando algo parecer errado

1. **GitHub → Actions**: o run vermelho mostra o passo exato que falhou.
2. **App Streamlit → "Ver logs no app"**: os logs sem sair do celular.
3. **Aba Notas** da planilha: última checagem do radar, coberturas de varredura,
   marcas de migração — a história operacional vive dentro do arquivo.
4. `ARCHITECTURE.md`: cada armadilha conhecida está documentada com data.

## 5. Desastres

- **Planilha do Drive corrompida/apagada:** o robô recomeça da semente do repo na
  próxima rodada; depois rode uma varredura 2018→hoje (~105 min) para completar.
- **PC formatado:** nada se perde — tudo vive no repo + Drive. Clone o repo e pronto.
- **Rodou algo errado na planilha:** os backups datados estão na pasta
  (`*_backup_AAAA-MM-DD.xlsx`); o Drive guarda versões (menu "Histórico de versões").

## Regras de ouro (não quebrar)

1. Nenhum dado inventado: célula de fora do DOU é **amarela** (cruzamento) ou
   **verde** (sua correção via Ajustes), com fonte na nota de cabeçalho.
2. Repositório público: **nunca** e-mail/senha/token no código — só GitHub Secrets.
3. Ato retificado `(*)` conta uma vez; a aba Atos guarda o histórico completo.
4. Vagas de INEP/e-MEC = total do curso existente, nunca o número de um pedido.
