# Start the local ordering dashboard (PowerShell, Windows or macOS): API on 127.0.0.1:8765, web app on http://127.0.0.1:5173
#   .\dashboard.ps1          real data (data\, outputs\ from `python run.py`)
#   .\dashboard.ps1 demo     synthetic demo (data_demo\, outputs_demo\ from `python run.py --generate --demo-harvest --today 2026-11-14`)
# Same behaviour as dashboard.sh. Everything stays on this computer: both servers listen on 127.0.0.1 only.
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File .\dashboard.ps1
param([string]$Mode = "")

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($Mode -eq "demo") {
    $env:DASH_DATA = "data_demo"
    $env:DASH_OUT = "outputs_demo"
} else {
    if (-not $env:DASH_DATA) { $env:DASH_DATA = "data" }
    if (-not $env:DASH_OUT) { $env:DASH_OUT = "outputs" }
}
if (-not $env:DASH_API_PORT) { $env:DASH_API_PORT = "8765" }

# Works on Windows and on macOS/Linux (run with `pwsh ./dashboard.ps1` there).
# $IsWindows only exists in PowerShell 6+; Windows PowerShell 5.1 ("Desktop") is always Windows.
$OnWindows = ($PSVersionTable.PSEdition -eq "Desktop") -or $IsWindows
if ($OnWindows) {
    $py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"; $pyFallback = "python"; $npm = "npm.cmd"
} else {
    $py = Join-Path $PSScriptRoot ".venv/bin/python"; $pyFallback = "python3"; $npm = "npm"
}
if (-not (Test-Path $py)) { $py = $pyFallback }

if (-not (Test-Path (Join-Path $env:DASH_OUT "brand_orders.csv"))) {
    Write-Error "No $(Join-Path $env:DASH_OUT brand_orders.csv) yet. Run python run.py first (see README)."
    exit 1
}
# Install web packages the first time, and again whenever package-lock.json changed (e.g. after a git pull).
$stamp = Join-Path "web" (Join-Path "node_modules" ".package-lock.json")
$lock = Join-Path "web" "package-lock.json"
if (-not (Test-Path $stamp) -or ((Get-Item -Force $lock).LastWriteTime -gt (Get-Item -Force $stamp).LastWriteTime)) {
    Push-Location web
    try { & $npm install --no-audit --no-fund } finally { Pop-Location }
}

$api = Start-Process -FilePath $py -NoNewWindow -PassThru -ArgumentList @(
    "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", $env:DASH_API_PORT, "--log-level", "warning")
# The LangChain deep agent runs inside the API above (the dashboard's "Ask" box); it needs
# OPENROUTER_API_KEY and LLM_MODEL in .env.
# The Photon iMessage bot is a separate process that answers texts with the same agent and the same data.
# It needs PHOTON_API_KEY and PHOTON_PROJECT_ID in .env; set DASH_NO_IMESSAGE=1 to skip it.
$bot = $null
if (-not $env:DASH_NO_IMESSAGE) {
    $env:AGENT_DATA = $env:DASH_DATA
    $env:AGENT_OUT = $env:DASH_OUT
    $bot = Start-Process -FilePath $py -NoNewWindow -PassThru -ArgumentList @("-m", "imessage.bot")
}
try {
    Write-Host "Data: $($env:DASH_DATA) / $($env:DASH_OUT)"
    Write-Host "Deep agent: in the dashboard's Ask box.  iMessage bot: $(if ($bot) { 'running (text the agent''s number)' } else { 'off' })"
    Write-Host "Open http://127.0.0.1:5173  (Ctrl+C to stop)"
    Push-Location web
    try { & $npm run dev } finally { Pop-Location }
} finally {
    foreach ($p in @($api, $bot)) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
}
