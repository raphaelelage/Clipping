# Runner self-hosted — PC do Raphael

Instalado em `C:\actions-runner`, registrado no repo `raphaelelage/Clipping`.
Como clipping.yml e blast.yml estao no MESMO repo, **um runner serve os dois**.

Labels: `self-hosted, windows, x64, pc-raphael`.

## Virar servico (sobe sozinho com o Windows)

Abra o PowerShell **como administrador** e rode:

    cd C:\actions-runner
    .\svc.cmd install
    .\svc.cmd start

Sem isso o runner so fica de pe enquanto a janela que o iniciou estiver aberta.

Conferir:  `.\svc.cmd status`   ·  Parar: `.\svc.cmd stop`

## O que ainda falta: o secret RUNNER_PAT

O job `escolher` (nos dois workflows) pergunta a API se existe runner online.
Essa consulta exige permissao **Administration: Read**, que o GITHUB_TOKEN padrao
NAO tem. Sem o secret, a pergunta falha, o job assume "PC desligado" e vai para o
GitHub — consumindo a cota de 2.000 min/mes. E de proposito: errar para o lado do
GitHub e melhor do que mandar o job para uma maquina que pode estar desligada.

Crie um **fine-grained PAT** com acesso so a este repositorio e so a permissao
`Administration: Read`:

    https://github.com/settings/personal-access-tokens/new

e salve como secret `RUNNER_PAT`:

    https://github.com/raphaelelage/Clipping/settings/secrets/actions

Evite usar um token classico de uso geral aqui: ele daria ao workflow muito mais
alcance do que a pergunta "o PC esta ligado?" precisa.
