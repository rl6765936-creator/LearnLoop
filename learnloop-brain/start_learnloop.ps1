# LearnLoop - starts both programs on THIS computer (decision 2026-11-24, Q8).
#   1. the brain, in live mode (your own data, encrypted, http://127.0.0.1:8765)
#   2. the Collector, watching the approved folder and sending to the brain automatically
# Run from PowerShell:   & ".\learnloop-brain\start_learnloop.ps1"  (from the LearnLoop folder)
# Nothing here enables an AI provider or the network: that stays a separate, explicit step.

$ErrorActionPreference = "Stop"
$brain     = $PSScriptRoot                                   # works wherever the LearnLoop folder is
$collector = Join-Path (Split-Path $PSScriptRoot -Parent) "learnloop-collector"
$token     = Join-Path $brain "local_data\ingest.token"
$port      = 8765

# Opens a NEW PowerShell window that runs $command. The command is passed encoded (-EncodedCommand)
# so quotes and paths with spaces or Hebrew letters cannot break it, and powershell.exe is called by
# its full path. If Windows refuses to open a window, the manual steps are printed instead.
function Open-Window([string]$title, [string]$command) {
    $full = "`$Host.UI.RawUI.WindowTitle = '$title'; $command"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($full))
    $exe = Join-Path $PSHOME "powershell.exe"
    try {
        Start-Process -FilePath $exe -ArgumentList "-NoExit -EncodedCommand $encoded" -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-Host ""
        Write-Host "Windows did not let this script open a new window ($($_.Exception.Message))."
        Write-Host "Open a new PowerShell window yourself and paste:"
        Write-Host "  $command"
        Write-Host ""
        return $false
    }
}

foreach ($p in @($brain, $collector)) {
    if (-not (Test-Path (Join-Path $p ".venv\Scripts\python.exe"))) { throw "Missing virtual environment in $p" }
}

function Test-Brain {
    try { Invoke-WebRequest "http://127.0.0.1:$port/api/system" -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

# 1) The brain - in its own window (skipped if it is already running, e.g. started by hand).
#    The FIRST start shows the recovery code once: write it on paper.
$brainCmd = "Set-Location '$brain'; `$env:PYTHONPATH = 'src'; & '.\.venv\Scripts\python.exe' -m brain.api.server --live --port $port"
if (Test-Brain) {
    Write-Host "The brain is already running."
} elseif (-not (Open-Window "LearnLoop - brain" $brainCmd)) {
    Write-Host "After the brain has started in that window, run this script again."
    return
}

# Wait until the brain has created its local token (first start: after you pressed Enter).
Write-Host "Waiting for the brain to start..."
for ($i = 0; $i -lt 300 -and -not (Test-Path $token); $i++) { Start-Sleep -Seconds 1 }
if (-not (Test-Path $token)) { throw "The brain did not start (no local token). Look at the brain window." }

# 2) The Collector - connect it to the brain (port + token PATH only), then watch in its own window.
Push-Location $collector
$env:PYTHONPATH = "src"
& ".\.venv\Scripts\python.exe" -m collector.main brain-link --port $port --token-file $token
Pop-Location
$collectorCmd = "Set-Location '$collector'; `$env:PYTHONPATH = 'src'; & '.\.venv\Scripts\python.exe' -m collector.main watch"
if (-not (Open-Window "LearnLoop - Collector" $collectorCmd)) { return }

# 3) Open the interface.
Start-Sleep -Seconds 2
try { Start-Process "http://127.0.0.1:$port" -ErrorAction Stop } catch { Write-Host "Open in the browser: http://127.0.0.1:$port" }
Write-Host "LearnLoop is running. To stop: press Ctrl+C in each of the two windows."
