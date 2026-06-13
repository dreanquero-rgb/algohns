@echo off
set "VAULT_DIR=%USERPROFILE%\.algohns"
echo Cancello chiavi eToro locali da %VAULT_DIR% ...
del "%VAULT_DIR%\ETORO_API_KEY.txt" >nul 2>nul
del "%VAULT_DIR%\ETORO_USER_KEY.txt" >nul 2>nul
del "%VAULT_DIR%\ETORO_BASE_URL.txt" >nul 2>nul
echo Fatto. Le secrets gia' caricate su Cloudflare non vengono cancellate da questo file.
pause
