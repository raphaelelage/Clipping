# Blast de Net Adds da ANS

Reconstroi as tabelas de net adds direto da **Sala de Situacao** da ANS e manda
por e-mail, com a planilha em anexo. **Nao usa o Caderno 2.0** — a ideia e pegar
o dado antes dele.

## Como roda

| | onde | o que faz | tempo |
|---|---|---|---|
| **fase 1** | PC e GitHub | grupos do `blast_grupos.json` + mercado | ~2 min |
| **fase 2** | so no PC | todas as operadoras + faixa etaria + UF | ~3 min |

Na nuvem roda so a fase 1 (`GITHUB_ACTIONS`), para nao gastar cota. No PC rodam
as duas, e **cada uma rende um e-mail**: o dos grupos chega em minutos, o
completo depois.

    Tarefa do Windows:  BBI net adds (08h30)  ->  rodar_netadds.bat
    App:                aba "ANS Net Adds"    ->  dispara blast.yml
    Log:                rodar_netadds.log

Quem coleta e o PC; quem **envia** e sempre o GitHub. O `.bat` grava a fase no
BigQuery e pede o e-mail correspondente
(`gh workflow run blast.yml -f modo=tabela -f fase=1`, depois `fase=2`), que usa
os secrets do clipping — nenhuma senha fica na maquina. E por isso que a fase 2
precisa caber no BQ inteira, inclusive as quebras: o `dimensao` da tabela existe
para isso, e quem le filtra uma dimensao so.

Se a fase 2 falhar, a tarefa termina com aviso no log — o primeiro e-mail ja
saiu. O e-mail que passar de 24 MB vai **sem anexo**, com as tabelas no corpo e
a planilha no artefato da execucao (o Gmail recusa a mensagem inteira, nao so o
anexo).

Os botoes do app nao coletam: remontam do BigQuery. "Base completa" e o segundo
e-mail, e so existe depois que a varredura do PC rodou.

## Arquivos

    blast_periodos.py   regra das colunas de Net Adds por mes
    blast_coleta.py     sessao sem cookies + qSerieTempo/qgrafbenef + quebras
    blast_historico.py  CSV/BQ -> formato longo
    blast_tabela.py     Lives · Net Adds · Base Growth
    blast_render.py     HTML do e-mail e a planilha
    blast_bq.py         historico no dataset sala_situacao_blast
    blast_textos.py     o rascunho de WhatsApp e as marcas {...}
    blast_run.py        orquestrador (--fase, --so-tabela, --mes)
    blast_app.py        a aba do app
    blast_grupos.json   grupos editaveis pelo app
    blast_colunas.json  12 meses x N colunas, editaveis pelo app
    blast_textos.json   os tres rascunhos, editaveis pelo app

## O rascunho de WhatsApp

Tres modelos, um por posicao do mes no trimestre (no 3o o trimestre fechou, e o
texto fala dele). O e-mail ja chega com o do mes certo preenchido — e o **corpo
de texto puro do e-mail e o proprio rascunho**, para copiar do celular sem abrir
o app.

A marca e uma chave entre `{}` com metrica, periodo e grupo, em qualquer ordem:

    {SULA net_adds QTD}      net adds da SULA no trimestre em curso (milhares)
    {Market lives}           vidas do mercado no mes de referencia
    {HAPV yoy}               Base Growth YoY, em %
    {odonto ODPV net_adds}   a mesma coisa na secao odontologica
    {rotulo Mês}             "Aug-26" — o rotulo, nao o numero

    metricas   lives · net_adds (padrao) · growth · mom · yoy · rotulo
    periodos   Mês (padrao) · QTD · Trimestre · YTD · Ano
    secoes     medico (padrao) · odonto · corporate · corporate_odonto
    extras     abs (vidas em vez de milhares) · mod (sem o sinal de menos)

O que nao e nenhuma dessas palavras vira nome de grupo — por isso `Porto Seguro`
funciona sem aspas. O numero sai da **tabela ja montada**, nao de uma segunda
conta: o texto e o print vao juntos no WhatsApp e nao podem discordar. Periodo
que nao e coluna do mes e calculado pela mesma `Serie` que a tabela usou; linha
residual ("Others") nesse caso nao tem como ser recalculada e sai `n.a.`.

Marca que nao resolve **nao desaparece**: fica `«assim»` no e-mail, com o motivo
na lista de avisos. A aba Textos valida antes de salvar e mostra como o robo leu
cada marca.

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
