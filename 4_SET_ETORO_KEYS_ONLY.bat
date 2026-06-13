@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "WORKER_NAME=algohns"
set "VAULT_DIR=%USERPROFILE%\.algohns"
if not exist "%VAULT_DIR%" mkdir "%VAULT_DIR%" >nul 2>nul
call npm install
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $dir=Join-Path $env:USERPROFILE '.algohns'; New-Item -ItemType Directory -Force -Path $dir | Out-Null; $api=Read-Host 'Incolla ETORO_API_KEY'; Set-Content -NoNewline -Encoding utf8 -Path (Join-Path $dir 'ETORO_API_KEY.txt') -Value $api.Trim(); $usr=Read-Host 'Incolla ETORO_USER_KEY'; Set-Content -NoNewline -Encoding utf8 -Path (Join-Path $dir 'ETORO_USER_KEY.txt') -Value $usr.Trim(); $base=Read-Host 'ETORO_BASE_URL opzionale, INVIO per lasciare default'; if($base.Trim()){ Set-Content -NoNewline -Encoding utf8 -Path (Join-Path $dir 'ETORO_BASE_URL.txt') -Value $base.Trim() }"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; (Get-Content -Raw (Join-Path $env:USERPROFILE '.algohns\ETORO_API_KEY.txt')).Trim() | npx wrangler secret put ETORO_API_KEY --name %WORKER_NAME%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; (Get-Content -Raw (Join-Path $env:USERPROFILE '.algohns\ETORO_USER_KEY.txt')).Trim() | npx wrangler secret put ETORO_USER_KEY --name %WORKER_NAME%"
echo Chiavi eToro salvate localmente e caricate su Cloudflare.
call npx wrangler secret list --name %WORKER_NAME%
pause
