# Blast de Net Adds da ANS

Reconstroi as tabelas de net adds direto da **Sala de Situacao** da ANS e manda
por e-mail, com a planilha em anexo. **Nao usa o Caderno 2.0** — a ideia e pegar
o dado antes dele.

## Como roda

| | onde | o que faz | tempo |
|---|---|---|---|
| **fase 1** | PC e GitHub | grupos do `blast_grupos.json` + mercado | ~2 min |
| **fase 2** | so no PC | todas as operadoras + faixa etaria + UF | ~3 min |

A decisao e automatica (`GITHUB_ACTIONS`): na nuvem so a fase 1, para nao gastar
cota; no PC as duas, mandando dois e-mails.

    Tarefa do Windows:  BBI net adds (08h30)  ->  rodar_netadds.bat
    App:                aba "ANS Net Adds"    ->  dispara blast.yml
    Log:                rodar_netadds.log

O `.bat` coleta e grava no BigQuery, depois pede o e-mail ao GitHub
(`gh workflow run blast.yml -f modo=tabela`), que usa os secrets do clipping.
Nenhuma senha fica na maquina.

## Arquivos

    blast_periodos.py   regra das colunas de Net Adds por mes
    blast_coleta.py     sessao sem cookies + qSerieTempo/qgrafbenef + quebras
    blast_historico.py  CSV/BQ -> formato longo
    blast_tabela.py     Lives · Net Adds · Base Growth
    blast_render.py     HTML do e-mail e a planilha
    blast_bq.py         historico no dataset sala_situacao_blast
    blast_run.py        orquestrador (--fase, --so-tabela, --mes)
    blast_app.py        a aba do app
    blast_grupos.json   grupos editaveis pelo app
    blast_colunas.json  12 meses x N colunas, editaveis pelo app

## Duas armadilhas da base (tambem na aba "Leia-me" da planilha)

1. **O registro 0 e o MERCADO**, nao uma operadora. Somar a coluna inteira conta
   o mercado duas vezes; filtre `registro <> 0`.
2. **A soma das operadoras nao chega ao mercado**: fica ~6% abaixo no medico e
   ~2,7% no odonto, estavel em todos os meses. Nao e falha da coleta — operadora
   a operadora o numero bate exato com o painel (conferido: Hapvida 4.350.014 em
   quatro queries diferentes). E o agregado da ANS que e maior que a soma das
   series que ele mesmo publica.

As abas de quebra sao MARGINAIS do mesmo total e nao se somam com a de
contratacao. Faixa etaria existe so por operadora e so para o mes corrente; o
mercado tem contratacao e UF, com historico.

## Medicoes (02/10/2026)

- 0,24 s por serie; o paralelismo **satura entre 4 e 10 robos** — com 20 ou 32 o
  tempo PIORA, porque o WAF serializa. Fixado em 8.
- varredura completa: 7.394 series em ~2,4 min · 3.697 operadoras no dropdown,
  **1.266 sem nenhum dado** (seguradoras antigas sem plano ativo)
- planilha completa: 21,6 MB -> 12,6 MB zipada (o Gmail corta em 25)
