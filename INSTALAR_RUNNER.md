# Rodar o Net Adds no PC — Agendador, nao runner

O Blast roda no PC do dono pelo **Agendador de Tarefas do Windows**, o mesmo
esquema das tarefas `BBI *` da Base Consolidada. Nao usa runner self-hosted.

    Tarefa:  BBI net adds        diaria, 08h30, limite de 2h
    Chama:   rodar_netadds.bat   (nesta pasta)
    Log:     rodar_netadds.log

Assim a coleta mensal nao gasta a cota de 2.000 min/mes do GitHub. O
`blast.yml` continua existindo para o disparo manual pelo app e como plano B.

## Credenciais

O `.bat` le `credenciais_netadds.bat` (NAO versionado). Copie o molde:

    copy credenciais_netadds.exemplo.bat credenciais_netadds.bat

e preencha `EMAIL_REMETENTE`, `EMAIL_SENHA` (senha de APP do Gmail, 16 letras,
em https://myaccount.google.com/apppasswords) e `BLAST_TO`.

Sem esse arquivo o `.bat` roda assim mesmo, mas com `--sem-email`: atualiza o
BigQuery e gera a planilha, sem enviar.

A credencial do BigQuery e a mesma do Cerebro
(`C:\Users\Raphael\Dev\Cerebro\_gcp\credenciais.json`), ja apontada pelo `.bat`.

## Por que nao runner self-hosted

Chegou a ser instalado em `C:\actions-runner` e foi removido. Para funcionar ele
exigiria duas pecas a mais — o servico do Windows (precisa de admin) e um secret
`RUNNER_PAT` com permissao Administration:Read, sem o qual o workflow nao
consegue nem perguntar se o PC esta ligado. O Agendador entrega o mesmo
resultado com o padrao que a casa ja usa.

## Conferir

    Get-ScheduledTask -TaskName "BBI net adds"
    Start-ScheduledTask -TaskName "BBI net adds"     # roda na hora
    Get-Content rodar_netadds.log -Tail 20
