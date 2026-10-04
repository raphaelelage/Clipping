@echo off
REM Blast de Net Adds da ANS no PC do dono (Agendador "BBI net adds").
REM Mesmo esquema das tarefas BBI da Base Consolidada.
REM
REM DIVISAO DE TRABALHO (dono, 02/10/2026):
REM   PC     -> a parte cara: coleta a Sala de Situacao e grava o historico no
REM             BigQuery. Nao gasta cota nenhuma.
REM   GitHub -> so o e-mail, via `gh workflow run blast.yml -f modo=tabela`, que
REM             le o historico do BQ e envia usando os secrets EMAIL_REMETENTE /
REM             EMAIL_SENHA que o clipping JA usa. Sem secret novo, sem senha
REM             guardada nesta maquina.
REM             Custa ~2 min de cota por rodada, contra ~6 min se o GitHub
REM             tambem coletasse.
REM
REM DUAS FASES, DOIS E-MAILS (dono, 02/10/2026 - fechado em 04/10/2026):
REM   fase 1  ~92 series (so os grupos) -> e-mail em poucos minutos
REM   fase 2  TODAS as operadoras + faixa etaria e UF -> segundo e-mail, com a
REM           planilha completa
REM   As duas gravam no BQ, e por isso o GitHub consegue montar os dois e-mails
REM   sem coletar nada. Se a fase 2 falhar, o primeiro e-mail ja foi: a tarefa
REM   termina com aviso, nao com a rodada perdida.
cd /d "%~dp0"
title BBI net adds - Blast da ANS (fecha sozinha ao terminar)
set PYTHONIOENCODING=utf-8

REM A credencial do BigQuery e a mesma que o Cerebro ja usa.
if not defined GOOGLE_APPLICATION_CREDENTIALS (
  set "GOOGLE_APPLICATION_CREDENTIALS=C:\Users\Raphael\Dev\Cerebro\_gcp\credenciais.json"
)

echo ==========================================
echo  BBI net adds - coleta da Sala de Situacao da ANS
echo  Inicio: %date% %time%   Log: rodar_netadds.log
echo ==========================================
echo ========================================== >> rodar_netadds.log
echo %date% %time%  INICIO >> rodar_netadds.log

REM ORFAO DA RODADA ANTERIOR: o Agendador encerra a TAREFA ao estourar o limite,
REM mas o python neto sobrevive solto — e dois coletores na mesma Sala de
REM Situacao e o caminho certo para tomar bloqueio do WAF. Mata SO o blast.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*blast_run*' } | ForEach-Object { Write-Host ('matando orfao PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >> rodar_netadds.log 2>&1

REM ---------------------------------------------------------------- FASE 1
REM --sem-email de proposito: quem envia e o GitHub, logo abaixo.
REM SEM PIPE: no cmd, o %errorlevel% depois de "A | B" e o de B. Com o antigo
REM "python | powershell Tee-Object", o RC testado era o do PowerShell (sempre
REM 0) e a protecao abaixo nunca disparava - a coleta podia falhar e o e-mail
REM era pedido assim mesmo, com o mes velho do BigQuery (medido em 04/10/2026).
REM Agora quem escreve o log e o proprio python, via --log.
echo.
echo [fase 1] grupos do de-para - alguns minutos
python -u blast_run.py --fase 1 --sem-email --log rodar_netadds.log %*
set RC=%errorlevel%
if %RC% neq 0 goto :erro

echo.
echo Historico no BigQuery atualizado. Pedindo o 1o e-mail ao GitHub...
call :pedir 1

REM ---------------------------------------------------------------- FASE 2
echo.
echo [fase 2] todas as operadoras + faixa etaria e UF - alguns minutos
python -u blast_run.py --fase 2 --sem-email --log rodar_netadds.log %*
set RC2=%errorlevel%
if %RC2% neq 0 goto :erro2

echo.
echo Base completa no BigQuery. Pedindo o 2o e-mail ao GitHub...
call :pedir 2

echo %date% %time%  OK (as duas fases) >> rodar_netadds.log
echo OK - terminou sem erros.
goto :fim

:pedir
REM %1 = fase. O e-mail sai do GitHub, que le o BQ e nao coleta nada.
gh workflow run blast.yml -f modo=tabela -f fase=%1 >> rodar_netadds.log 2>&1
if errorlevel 1 (
  echo *** nao consegui disparar o e-mail da fase %1 - rode a mao pelo app ***
  echo %date% %time%  ERRO ao disparar o e-mail da fase %1 >> rodar_netadds.log
) else (
  echo E-mail da fase %1 solicitado - chega em ~2 min.
  echo %date% %time%  e-mail da fase %1 solicitado ao GitHub >> rodar_netadds.log
)
exit /b 0

:erro2
REM a fase 1 ja rendeu e-mail; perder a fase 2 nao desfaz a rodada
echo %date% %time%  ATENCAO - erro na fase 2. O 1o e-mail saiu. >> rodar_netadds.log
echo *** ATENCAO - erro na fase 2 (base completa). Veja rodar_netadds.log ***
goto :fim

:erro
echo %date% %time%  ATENCAO - erro na fase 1, e-mail NAO solicitado >> rodar_netadds.log
echo *** ATENCAO - erro na coleta. Veja rodar_netadds.log ***

:fim
timeout /t 10 /nobreak >nul
