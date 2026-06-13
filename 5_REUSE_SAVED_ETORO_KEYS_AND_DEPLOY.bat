@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "WORKER_NAME=algohns"
set "VAULT_DIR=%USERPROFILE%\.algohns"
call npm install
if not exist "%VAULT_DIR%\ETORO_API_KEY.txt" (
  echo [ERRORE] ETORO_API_KEY non trovata in %VAULT_DIR%.
  echo Usa prima 4_SET_ETORO_KEYS_ONLY.bat.
  pause
  exit /b 1
)
if not exist "%VAULT_DIR%\ETORO_USER_KEY.txt" (
  echo [ERRORE] ETORO_USER_KEY non trovata in %VAULT_DIR%.
  echo Usa prima 4_SET_ETORO_KEYS_ONLY.bat.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; (Get-Content -Raw (Join-Path $env:USERPROFILE '.algohns\ETORO_API_KEY.txt')).Trim() | npx wrangler secret put ETORO_API_KEY --name %WORKER_NAME%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; (Get-Content -Raw (Join-Path $env:USERPROFILE '.algohns\ETORO_USER_KEY.txt')).Trim() | npx wrangler secret put ETORO_USER_KEY --name %WORKER_NAME%"
if exist "%VAULT_DIR%\ETORO_BASE_URL.txt" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $p=Join-Path $env:USERPROFILE '.algohns\ETORO_BASE_URL.txt'; $v=(Get-Content -Raw $p).Trim(); if($v){ $v | npx wrangler secret put ETORO_BASE_URL --name %WORKER_NAME% }"
)
call npx wrangler deploy --name %WORKER_NAME%
pause
