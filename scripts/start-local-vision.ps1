$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $root 'data\local-vision'
$server = Join-Path $root 'data\local-model\runtime\llama-server.exe'
$model = Join-Path $localDir 'Qwen3-VL-2B-Instruct-Q4_K_M.gguf'
$projector = Join-Path $localDir 'mmproj-F16.gguf'
$pidFile = Join-Path $localDir 'server-process.json'
foreach ($file in @($server, $model, $projector)) {
    if (!(Test-Path -LiteralPath $file)) {
        throw 'Run setup-local-model.ps1 and .venv/Scripts/python.exe scripts/setup-local-vision.py first.'
    }
}
if (Test-Path -LiteralPath $pidFile) {
    $saved = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    $existing = Get-Process -Id $saved.id -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $server -and
        $existing.StartTime.ToUniversalTime().Ticks -eq $saved.startedTicks) {
        Write-Output "Local vision model already running (PID $($existing.Id))."
        exit 0
    }
}
if ((Get-FileHash -LiteralPath $model -Algorithm SHA256).Hash -ne
    '858fcf2a39dc73b26dd86592cb0a5f949b59d1edb365d1dea98e46b02e955e56' -or
    (Get-FileHash -LiteralPath $projector -Algorithm SHA256).Hash -ne
    'cd5a851d3928697fa1bd76d459d2cc409b6cf40c9d9682b2f5c8e7c6a9f9630f') {
    throw 'Vision model checksum mismatch; run setup-local-vision.py.'
}
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 18081)
try { $listener.Start() } finally { $listener.Stop() }
$arguments = @('-m', ('"' + $model + '"'), '--mmproj', ('"' + $projector + '"'),
               '--host', '127.0.0.1', '--port', '18081', '--alias', 'paintai-vision',
               '--ctx-size', '8192', '--parallel', '1', '--threads', '8',
               '--gpu-layers', '0', '--no-mmproj-offload', '--image-max-tokens', '1024')
$process = Start-Process -FilePath $server -ArgumentList $arguments -WorkingDirectory $localDir `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $localDir 'server.stdout.log') `
    -RedirectStandardError (Join-Path $localDir 'server.stderr.log')
@{ id = $process.Id; startedTicks = $process.StartTime.ToUniversalTime().Ticks } |
    ConvertTo-Json | Set-Content -LiteralPath $pidFile
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if ($process.HasExited) { throw 'Vision server exited; inspect data/local-vision/server.stderr.log.' }
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:18081/health' -TimeoutSec 1
        if ($health.status -eq 'ok') {
            Write-Output "Vision ready: http://127.0.0.1:18081/v1 (PID $($process.Id))"
            exit 0
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
throw 'Vision server is still starting; inspect data/local-vision/server.stderr.log.'
