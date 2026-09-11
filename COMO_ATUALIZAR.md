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

---

## 1. Varredura por período (backfill / reconstrução)

**Quando:** suspeita de buraco, ou quer refazer um intervalo.
**Onde:** aba **"Varredura DOU"** do app Streamlit (datas + e-mail + Rodar agora /
agendar) · ou no PC: `Varredura DOU.bat` · ou GitHub → Actions → `varredura-dou`.
**Custo:** ~2,6s/dia útil (+ ~4 min de Funil). 1 mês ≈ 5 min · 2018–hoje ≈ 105 min.
**Garantia:** dia que falhar sai NOMINALMENTE no e-mail; rode de novo o intervalo.
A cobertura fica gravada na aba **Notas** (`varredura_cobertura`).

## 2. Correção manual de dados (cod_ies, município, vagas...)

**NUNCA edite o Funil direto** — ele é regenerado e a edição evapora.
Aba **Ajustes**: uma linha com `link` (copie da coluna link_fonte) + `campo` +
`valor` (+ `curso` se o ato tiver vários). Vira célula **VERDE** para sempre.
Onde pesquisar o valor certo: o próprio ato (link_fonte), o CSV do e-MEC na pasta
(Ctrl+F/PROCV) ou emec.mec.gov.br.

## 3. Manutenção periódica (bases de cruzamento)

| Quando | Comando (na pasta `C:\Users\Raphael\Dev\Clipping`) |
|---|---|
| E-mail avisar ENAMED novo | `python atualizar_cautelares.py <link1> <link2>` |
| 1-2×/ano (e-MEC) | baixar o CSV¹ → `python atualizar_emec.py` |
| Censo novo do INEP (anual) | baixar o zip de microdados → `python atualizar_inep.py <zip>` |
| SERES publicar planilha nova | abrir **PROMPT_ATUALIZAR_SERES.md** e colar numa sessão do Claude |

Depois de qualquer um: `git add *.parquet *.json && git commit -m "atualiza base X" && git push`
— o robô usa os arquivos do repositório.

¹ CSV "Cursos de Graduação do Brasil": https://dadosabertos.mec.gov.br/indicadores-sobre-ensino-superior/item/183-cursos-de-graduacao-do-brasil (o portal tem CAPTCHA — baixe no navegador e salve na pasta `Clipping New`).

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
