$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $root 'data\local-model'
$pidFile = Join-Path $localDir 'server-process.json'
$server = Join-Path $localDir 'runtime\llama-server.exe'
if (!(Test-Path -LiteralPath $pidFile)) { Write-Output 'No managed local server.'; exit 0 }
$saved = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
$process = Get-Process -Id $saved.id -ErrorAction SilentlyContinue
if ($process) {
    if ($process.Path -ne $server -or
        $process.StartTime.ToUniversalTime().Ticks -ne $saved.startedTicks) {
        throw 'Process identity changed; refusing to stop an unrelated process.'
    }
    Stop-Process -Id $process.Id
}
Remove-Item -LiteralPath $pidFile
Write-Output 'Local model stopped.'
