@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "WORKER_NAME=algohns"
set "VAULT_DIR=%USERPROFILE%\.algohns"

echo ==========================================================
echo  ALGOHNS V9.2 DUAL BROKER - INSTALL + ETORO KEYS + DEPLOY
echo ==========================================================
echo.
echo Questa procedura fa:
echo  1. installazione dipendenze npm
echo  2. controllo Wrangler tramite npx
echo  3. salvataggio locale chiavi eToro in %%USERPROFILE%%\.algohns
echo  4. upload secrets su Cloudflare Worker: %WORKER_NAME%
echo  5. deploy automatico su https://algohns.dreanquero.workers.dev/
echo.
echo Le chiavi salvate localmente verranno riusate anche nelle prossime versioni.
echo.

where node >nul 2>nul
if errorlevel 1 (
  echo [ERRORE] Node.js non trovato. Installa Node.js LTS e rilancia questo file.
  pause
  exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [ERRORE] npm non trovato. Installa Node.js LTS e rilancia questo file.
  pause
  exit /b 1
)

if not exist "%VAULT_DIR%" mkdir "%VAULT_DIR%" >nul 2>nul

echo.
echo [1/5] Installo dipendenze locali, incluso Wrangler...
call npm install
if errorlevel 1 (
  echo [ERRORE] npm install fallito.
  pause
  exit /b 1
)

echo.
echo [2/5] Controllo login Cloudflare/Wrangler...
call npx wrangler whoami >nul 2>nul
if errorlevel 1 (
  echo Non risulti loggato su Cloudflare. Apro login Wrangler...
  call npx wrangler login
  if errorlevel 1 (
    echo [ERRORE] Login Wrangler fallito.
    pause
    exit /b 1
  )
) else (
  echo Wrangler OK.
)

set "NEED_KEYS=0"
if not exist "%VAULT_DIR%\ETORO_API_KEY.txt" set "NEED_KEYS=1"
if not exist "%VAULT_DIR%\ETORO_USER_KEY.txt" set "NEED_KEYS=1"

if "%NEED_KEYS%"=="1" (
  echo.
  echo [3/5] Mancano chiavi eToro salvate localmente.
  echo Inseriscile ora. Non incollarle in chat: solo qui nel terminale.
  call :SAVE_ETORO_KEYS
) else (
  echo.
  echo [3/5] Trovate chiavi eToro gia' salvate in %VAULT_DIR%.
  set /p UPDATE_KEYS="Vuoi sovrascriverle? (S/N): "
  if /I "%UPDATE_KEYS%"=="S" call :SAVE_ETORO_KEYS
)

if not exist "%VAULT_DIR%\ETORO_API_KEY.txt" (
  echo [ERRORE] ETORO_API_KEY non salvata.
  pause
  exit /b 1
)
if not exist "%VAULT_DIR%\ETORO_USER_KEY.txt" (
  echo [ERRORE] ETORO_USER_KEY non salvata.
  pause
  exit /b 1
)

echo.
echo [4/5] Carico le chiavi eToro come Cloudflare secrets sul Worker %WORKER_NAME%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $p=Join-Path $env:USERPROFILE '.algohns\ETORO_API_KEY.txt'; (Get-Content -Raw $p).Trim() | npx wrangler secret put ETORO_API_KEY --name %WORKER_NAME%"
if errorlevel 1 (
  echo [ERRORE] Upload ETORO_API_KEY fallito.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $p=Join-Path $env:USERPROFILE '.algohns\ETORO_USER_KEY.txt'; (Get-Content -Raw $p).Trim() | npx wrangler secret put ETORO_USER_KEY --name %WORKER_NAME%"
if errorlevel 1 (
  echo [ERRORE] Upload ETORO_USER_KEY fallito.
  pause
  exit /b 1
)
if exist "%VAULT_DIR%\ETORO_BASE_URL.txt" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $p=Join-Path $env:USERPROFILE '.algohns\ETORO_BASE_URL.txt'; $v=(Get-Content -Raw $p).Trim(); if($v){ $v | npx wrangler secret put ETORO_BASE_URL --name %WORKER_NAME% }"
)

echo.
echo Secrets presenti sul Worker:
call npx wrangler secret list --name %WORKER_NAME%

echo.
echo [5/5] Deploy automatico su Worker %WORKER_NAME%...
call npx wrangler deploy --name %WORKER_NAME%
if errorlevel 1 (
  echo [ERRORE] Deploy fallito.
  pause
  exit /b 1
)

echo.
echo ==========================================================
echo  FATTO. Apri il sito e fai CTRL+F5:
echo  https://algohns.dreanquero.workers.dev/
echo.
echo  Test diretto eToro:
echo  https://algohns.dreanquero.workers.dev/api/broker/etoro/status
echo ==========================================================
pause
exit /b 0

:SAVE_ETORO_KEYS
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $dir=Join-Path $env:USERPROFILE '.algohns'; New-Item -ItemType Directory -Force -Path $dir | Out-Null; $api=Read-Host 'Incolla ETORO_API_KEY'; Set-Content -NoNewline -Encoding utf8 -Path (Join-Path $dir 'ETORO_API_KEY.txt') -Value $api.Trim(); $usr=Read-Host 'Incolla ETORO_USER_KEY'; Set-Content -NoNewline -Encoding utf8 -Path (Join-Path $dir 'ETORO_USER_KEY.txt') -Value $usr.Trim(); $base=Read-Host 'ETORO_BASE_URL opzionale, premi INVIO per default'; if($base.Trim()){ Set-Content -NoNewline -Encoding utf8 -Path (Join-Path $dir 'ETORO_BASE_URL.txt') -Value $base.Trim() }"
exit /b 0
