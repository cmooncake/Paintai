$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
foreach ($name in @('web','api')) {
    $recordPath = Join-Path $root "data\web\$name-process.json"
    if (!(Test-Path -LiteralPath $recordPath)) { continue }
    $saved = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
    $process = Get-Process -Id $saved.id -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq $saved.path -and
        $process.StartTime.ToUniversalTime().Ticks -eq $saved.startedTicks) {
        # The Windows venv launcher and Vite can own child processes.
        # Verify the saved process identity before stopping its entire tree.
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not stop $name process tree." }
        Write-Output "Stopped $name (PID $($process.Id))."
    }
    Remove-Item -LiteralPath $recordPath
}
