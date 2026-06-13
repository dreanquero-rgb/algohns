@echo off
cd /d "%~dp0"
echo Deploy Algohns V9.2 Dual Broker sul Worker algohns...
call npm install
if errorlevel 1 exit /b 1
call npx wrangler deploy --name algohns
pause
