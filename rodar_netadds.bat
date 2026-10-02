@echo off
REM Blast de Net Adds da ANS no PC do dono (Agendador "BBI net adds").
REM Mesmo esquema das tarefas BBI da Base Consolidada: roda local, sem gastar a
REM cota do GitHub. O workflow blast.yml continua existindo como plano B e para
REM o disparo manual pelo app.
REM
REM Credenciais: credenciais_netadds.bat ao lado deste arquivo (NAO versionado).
REM Use credenciais_netadds.exemplo.bat como molde.
cd /d "%~dp0"
title BBI net adds - Blast da ANS (fecha sozinha ao terminar)
set PYTHONIOENCODING=utf-8

if exist "credenciais_netadds.bat" (
  call "credenciais_netadds.bat"
) else (
  echo *** credenciais_netadds.bat NAO existe - vai rodar SEM enviar e-mail ***
  set BLAST_SEM_EMAIL=--sem-email
)

REM A credencial do BigQuery e a mesma que o Cerebro ja usa.
if not defined GOOGLE_APPLICATION_CREDENTIALS (
  set "GOOGLE_APPLICATION_CREDENTIALS=C:\Users\Raphael\Dev\Cerebro\_gcp\credenciais.json"
)

echo ==========================================
echo  BBI net adds - Blast da ANS
echo  Inicio: %date% %time%   Log: rodar_netadds.log
echo ==========================================
echo ========================================== >> rodar_netadds.log
echo %date% %time%  INICIO >> rodar_netadds.log

REM ORFAO DA RODADA ANTERIOR: o Agendador encerra a TAREFA ao estourar o limite,
REM mas o python neto sobrevive solto — e dois coletores na mesma Sala de
REM Situacao e o caminho certo para tomar bloqueio do WAF. Mata SO o blast.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*blast_run*' } | ForEach-Object { Write-Host ('matando orfao PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >> rodar_netadds.log 2>&1

python -u blast_run.py %BLAST_SEM_EMAIL% %* 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath rodar_netadds.log -Append"
set RC=%errorlevel%

echo %date% %time%  FIM >> rodar_netadds.log
if %RC% neq 0 (
  echo %date% %time%  ATENCAO - houve erro neste run >> rodar_netadds.log
  echo *** ATENCAO - houve erro. Veja rodar_netadds.log ***
) else (
  echo %date% %time%  OK >> rodar_netadds.log
  echo OK - terminou sem erros.
)
timeout /t 10 /nobreak >nul
