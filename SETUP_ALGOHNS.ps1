param(
  [switch]$KeysOnly,
  [switch]$DeployOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Write-Step($msg) { Write-Host "`n  $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [ERRORE] $msg" -ForegroundColor Red; Read-Host "  Premi Invio per uscire"; exit 1 }
function Find-Cmd($names) { foreach ($n in $names) { $c = Get-Command $n -ErrorAction SilentlyContinue; if ($c) { return $c.Source } } return $null }
function Run-Checked($cmdPath, [string[]]$cmdArgs, $failMsg) { & $cmdPath @cmdArgs; if ($LASTEXITCODE -ne 0) { Fail $failMsg } }

Clear-Host
Write-Host "  =====================================================" -ForegroundColor DarkCyan
Write-Host "   ALGOHNS V11 - Alpaca Paper Setup e Deploy" -ForegroundColor White
Write-Host "  =====================================================" -ForegroundColor DarkCyan

$node = Find-Cmd @('node.exe','node')
$npm = Find-Cmd @('npm.cmd','npm')
$npx = Find-Cmd @('npx.cmd','npx')
if (-not $node) { Fail "Node.js non trovato. Installa Node.js LTS da nodejs.org" }
if (-not $npm) { Fail "npm non trovato. Reinstalla Node.js LTS selezionando Add to PATH" }
if (-not $npx) { Fail "npx non trovato. Reinstalla Node.js LTS selezionando Add to PATH" }

Write-Host ""
Write-Host "  Node.js: $(& $node --version)"
Write-Host "  npm: $(& $npm --version)"
Write-Host "  node path: $node"
Write-Host "  npm path:  $npm"
Write-Host "  npx path:  $npx"

Write-Step "Installo/aggiorno Wrangler 4 nel progetto..."
Run-Checked $npm @('install','--save-dev','wrangler@4','--no-audit','--no-fund') "npm install --save-dev wrangler@4 fallito"
Run-Checked $npx @('wrangler','--version') "Wrangler non parte correttamente"

$storeDir = Join-Path $env:USERPROFILE '.algohns'
$envFile = Join-Path $storeDir 'alpaca.env'
if (-not (Test-Path $storeDir)) { New-Item -ItemType Directory -Path $storeDir | Out-Null }

function Read-LocalKeys {
  $out = @{}
  if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
      if ($_ -match '^([^#=]+)=(.*)$') { $out[$matches[1].Trim()] = $matches[2].Trim() }
    }
  }
  return $out
}
function Save-LocalKeys($apiKey, $secretKey) {
  @(
    "ALPACA_API_KEY=$apiKey",
    "ALPACA_SECRET_KEY=$secretKey",
    "ALPACA_BASE_URL=https://paper-api.alpaca.markets"
  ) | Set-Content -Path $envFile -Encoding UTF8
  Write-Ok "Chiavi salvate localmente in $envFile"
}
function Put-Secret($name, $value) {
  Write-Host "  Salvo secret Cloudflare: $name"
  $value | & $npx wrangler secret put $name --name algohns
  if ($LASTEXITCODE -ne 0) { Fail "wrangler secret put $name fallito" }
}
function Cloudflare-HasAlpacaSecrets {
  try {
    $list = & $npx wrangler secret list --name algohns 2>$null | Out-String
    return ($list -match 'ALPACA_API_KEY' -and $list -match 'ALPACA_SECRET_KEY')
  } catch { return $false }
}

if (-not $DeployOnly) {
  Write-Step "Controllo chiavi Alpaca Paper..."
  $local = Read-LocalKeys
  $hasLocal = $local.ContainsKey('ALPACA_API_KEY') -and $local.ContainsKey('ALPACA_SECRET_KEY') -and $local['ALPACA_API_KEY'] -and $local['ALPACA_SECRET_KEY']
  $hasCloud = Cloudflare-HasAlpacaSecrets

  if ($hasLocal) {
    Write-Ok "Chiavi locali trovate in %USERPROFILE%\.algohns"
    $ans = Read-Host "  Vuoi riusarle e sincronizzarle su Cloudflare? [S/n]"
    if ($ans -eq '' -or $ans.ToLower().StartsWith('s') -or $ans.ToLower().StartsWith('y')) {
      Put-Secret 'ALPACA_API_KEY' $local['ALPACA_API_KEY']
      Put-Secret 'ALPACA_SECRET_KEY' $local['ALPACA_SECRET_KEY']
    } else {
      $apiKey = Read-Host "  Inserisci nuova ALPACA_API_KEY"
      $secretKey = Read-Host "  Inserisci nuova ALPACA_SECRET_KEY"
      if (-not $apiKey -or -not $secretKey) { Fail "Chiavi mancanti" }
      Save-LocalKeys $apiKey $secretKey
      Put-Secret 'ALPACA_API_KEY' $apiKey
      Put-Secret 'ALPACA_SECRET_KEY' $secretKey
    }
  } elseif ($hasCloud) {
    Write-Ok "Secrets Alpaca già presenti su Cloudflare"
    $ans = Read-Host "  Vuoi mantenerli senza reinserire le chiavi? [S/n]"
    if ($ans -ne '' -and ($ans.ToLower().StartsWith('n'))) {
      $apiKey = Read-Host "  Inserisci ALPACA_API_KEY"
      $secretKey = Read-Host "  Inserisci ALPACA_SECRET_KEY"
      if (-not $apiKey -or -not $secretKey) { Fail "Chiavi mancanti" }
      Save-LocalKeys $apiKey $secretKey
      Put-Secret 'ALPACA_API_KEY' $apiKey
      Put-Secret 'ALPACA_SECRET_KEY' $secretKey
    } else {
      Write-Ok "Mantengo i secrets già presenti su Cloudflare"
    }
  } else {
    Write-Warn "Nessuna chiave Alpaca trovata localmente o su Cloudflare"
    $apiKey = Read-Host "  Inserisci ALPACA_API_KEY"
    $secretKey = Read-Host "  Inserisci ALPACA_SECRET_KEY"
    if (-not $apiKey -or -not $secretKey) { Fail "Chiavi mancanti" }
    Save-LocalKeys $apiKey $secretKey
    Put-Secret 'ALPACA_API_KEY' $apiKey
    Put-Secret 'ALPACA_SECRET_KEY' $secretKey
  }
}

if ($KeysOnly) {
  Write-Ok "Configurazione chiavi completata."
  Read-Host "  Premi Invio per uscire"
  exit 0
}

Write-Step "Controllo sintassi JavaScript..."
Run-Checked $node @('--check','_worker.js') "_worker.js contiene errori di sintassi"
Run-Checked $node @('--check','public/assets/app.js') "public/assets/app.js contiene errori di sintassi"

Write-Step "Deploy su Cloudflare Worker: algohns"
Run-Checked $npx @('wrangler','deploy','--name','algohns') "wrangler deploy fallito"

Write-Host ""
Write-Ok "Deploy completato. Apri: https://algohns.dreanquero.workers.dev"
Write-Host "  Dopo il deploy fai CTRL + F5 e controlla Settings / Connections -> Live build check."
Read-Host "  Premi Invio per uscire"
