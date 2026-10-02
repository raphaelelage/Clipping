# Rodar o Net Adds no PC — Agendador, nao runner

    Tarefa:  BBI net adds        diaria, 08h30, limite de 2h
    Chama:   rodar_netadds.bat   (nesta pasta)
    Log:     rodar_netadds.log

## Quem faz o que

| etapa | onde | por que |
|---|---|---|
| coletar as ~92 series e gravar no BigQuery | **PC** | e a parte cara (~4 min). Nao gasta cota |
| montar as tabelas e enviar o e-mail | **GitHub** | reusa os secrets EMAIL_REMETENTE / EMAIL_SENHA que o clipping ja tem. ~2 min de cota |

O `.bat` termina chamando `gh workflow run blast.yml -f modo=tabela`. Esse modo
NAO consulta a ANS: le o historico que o PC acabou de gravar no BigQuery, monta
as tres tabelas e manda.

**Nenhuma senha fica nesta maquina** e nenhum secret novo foi criado. Se a coleta
falhar, o e-mail nao e pedido — melhor nao receber do que receber com o mes velho.

A credencial do BigQuery e a mesma do Cerebro
(`C:\Users\Raphael\Dev\Cerebro\_gcp\credenciais.json`), ja apontada pelo `.bat`.

## Conferir

    Get-ScheduledTask -TaskName "BBI net adds"
    Start-ScheduledTask -TaskName "BBI net adds"     # roda na hora
    Get-Content rodar_netadds.log -Tail 20

## Por que nao runner self-hosted

Chegou a ser instalado em `C:\actions-runner` e foi removido. Exigiria o servico
do Windows (admin) e um secret `RUNNER_PAT` com Administration:Read, sem o qual o
workflow nao consegue nem perguntar se o PC esta ligado. O Agendador entrega o
mesmo resultado com o padrao que a casa ja usa.
