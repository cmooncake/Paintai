$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $root 'data\local-model'
$server = Join-Path $localDir 'runtime\llama-server.exe'
$model = Join-Path $localDir 'qwen2.5-0.5b-instruct-q4_k_m.gguf'
$pidFile = Join-Path $localDir 'server-process.json'
if (!(Test-Path -LiteralPath $server) -or !(Test-Path -LiteralPath $model)) {
    throw 'Run scripts/setup-local-model.ps1 first.'
}
if (Test-Path -LiteralPath $pidFile) {
    $saved = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    $existing = Get-Process -Id $saved.id -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $server -and
        $existing.StartTime.ToUniversalTime().Ticks -eq $saved.startedTicks) {
        Write-Output "Local model already running (PID $($existing.Id))."
        exit 0
    }
}
if ((Get-FileHash -LiteralPath $model -Algorithm SHA256).Hash -ne
    '74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db') {
    throw 'Model checksum mismatch; rerun scripts/setup-local-model.ps1.'
}
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 18080)
try { $listener.Start() } finally { $listener.Stop() }
$arguments = @('-m', ('"' + $model + '"'), '--host', '127.0.0.1', '--port', '18080',
               '--alias', 'paintai-local', '--ctx-size', '4096', '--parallel', '1',
               '--threads', '4', '--gpu-layers', '0')
$process = Start-Process -FilePath $server -ArgumentList $arguments -WorkingDirectory $localDir `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $localDir 'server.stdout.log') `
    -RedirectStandardError (Join-Path $localDir 'server.stderr.log')
@{ id = $process.Id; startedTicks = $process.StartTime.ToUniversalTime().Ticks } |
    ConvertTo-Json | Set-Content -LiteralPath $pidFile
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    if ($process.HasExited) { throw 'Local server exited; inspect data/local-model/server.stderr.log.' }
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:18080/health' -TimeoutSec 1
        if ($health.status -eq 'ok') {
            Write-Output "Ready: http://127.0.0.1:18080/v1 (PID $($process.Id))"
            exit 0
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
throw 'Server is still starting; inspect data/local-model/server.stderr.log.'
