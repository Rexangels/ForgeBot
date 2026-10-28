# Start the ForgeBot local stack: API server plus the durable worker.
# Run this from the workspace root instead of starting each process by hand:
#   powershell -ExecutionPolicy Bypass -File .\start-dev.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Python not found at $python. Adjust the path at the top of this script."
}

$worker = Start-Process -FilePath $python -ArgumentList "manage.py", "runworker" -WorkingDirectory $backend -PassThru -WindowStyle Minimized
Write-Host "Worker started (pid $($worker.Id)). Starting API server; stop this window to stop it."

try {
    & $python manage.py runserver
} finally {
    if (-not $worker.HasExited) {
        Stop-Process -Id $worker.Id -Force
        Write-Host "Worker stopped."
    }
}
