# Start the local ordering dashboard on Windows (PowerShell): API on 127.0.0.1:8765, web app on http://127.0.0.1:5173
#   .\dashboard.ps1          real data (data\, outputs\ from `python run.py`)
#   .\dashboard.ps1 demo     synthetic demo (data_demo\, outputs_demo\ from `python run.py --generate --today 2026-11-14`)
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

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

if (-not (Test-Path (Join-Path $env:DASH_OUT "brand_orders.csv"))) {
    Write-Error "No $($env:DASH_OUT)\brand_orders.csv yet. Run python run.py first (see README)."
    exit 1
}
if (-not (Test-Path "web\node_modules")) {
    Push-Location web
    try { npm.cmd install --no-audit --no-fund } finally { Pop-Location }
}

$api = Start-Process -FilePath $py -NoNewWindow -PassThru -ArgumentList @(
    "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", $env:DASH_API_PORT, "--log-level", "warning")
try {
    Write-Host "Data: $($env:DASH_DATA) / $($env:DASH_OUT)"
    Write-Host "Open http://127.0.0.1:5173  (Ctrl+C to stop)"
    Push-Location web
    try { npm.cmd run dev } finally { Pop-Location }
} finally {
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
}
