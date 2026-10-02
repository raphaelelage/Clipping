@echo off
REM Blast de Net Adds da ANS no PC do dono (Agendador "BBI net adds").
REM Mesmo esquema das tarefas BBI da Base Consolidada.
REM
REM DIVISAO DE TRABALHO (dono, 02/10/2026):
REM   PC     -> a parte cara: coleta as ~92 series da Sala de Situacao e grava
REM             o historico no BigQuery. Nao gasta cota nenhuma.
REM   GitHub -> so o e-mail, via `gh workflow run blast.yml -f modo=tabela`, que
REM             le o historico do BQ e envia usando os secrets EMAIL_REMETENTE /
REM             EMAIL_SENHA que o clipping JA usa. Sem secret novo, sem senha
REM             guardada nesta maquina.
REM             Custa ~2 min de cota por rodada, contra ~6 min se o GitHub
REM             tambem coletasse.
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

REM --sem-email de proposito: quem envia e o GitHub, logo abaixo.
python -u blast_run.py --sem-email %* 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath rodar_netadds.log -Append"
set RC=%errorlevel%

if %RC% neq 0 goto :erro

echo.
echo Historico no BigQuery atualizado. Pedindo o e-mail ao GitHub...
gh workflow run blast.yml -f modo=tabela >> rodar_netadds.log 2>&1
if errorlevel 1 (
  echo *** nao consegui disparar o e-mail pelo GitHub - rode a mao pelo app ***
  echo %date% %time%  ERRO ao disparar o e-mail >> rodar_netadds.log
) else (
  echo E-mail solicitado - chega em ~2 min.
  echo %date% %time%  e-mail solicitado ao GitHub >> rodar_netadds.log
)
echo %date% %time%  OK >> rodar_netadds.log
echo OK - terminou sem erros.
goto :fim

:erro
echo %date% %time%  ATENCAO - erro na coleta, e-mail NAO solicitado >> rodar_netadds.log
echo *** ATENCAO - erro na coleta. Veja rodar_netadds.log ***

:fim
timeout /t 10 /nobreak >nul
