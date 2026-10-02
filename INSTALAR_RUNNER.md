# Runner self-hosted — PC do Raphael

Instalado em `C:\actions-runner`, registrado no repo `raphaelelage/Clipping`.
Como `clipping.yml` e `blast.yml` estao no MESMO repo, **um runner serve os dois**.

Labels: `self-hosted, windows, x64, pc-raphael`.

## Virar servico (sobe sozinho com o Windows)

**`svc.cmd` NAO existe nas versoes atuais do runner (2.3x).** Ele so e criado
quando o runner ja foi configurado como servico — ou seja, instalar o servico
significa RE-REGISTRAR o runner com `--runasservice`, nao rodar um script a parte.

PowerShell **como administrador**. O comando busca o token sozinho, para ele nao
passar por chat nem ficar em disco:

```
cd C:\actions-runner
$t = gh api repos/raphaelelage/Clipping/actions/runners/registration-token -X POST --jq .token
.\config.cmd --unattended --replace --url https://github.com/raphaelelage/Clipping --token $t --name pc-raphael --labels self-hosted,windows,x64,pc-raphael --work _work --runasservice
```

Conferir: `Get-Service actions.runner.*`
Parar / subir: `Stop-Service actions.runner.*` · `Start-Service actions.runner.*`

Sem o servico, o runner so fica de pe enquanto o processo que o iniciou viver.

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

Evite um token classico de uso geral aqui: ele daria ao workflow muito mais
alcance do que a pergunta "o PC esta ligado?" precisa.
