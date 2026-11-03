param([switch]$Offline, [switch]$StartModels)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$vite = Join-Path $root 'web\node_modules\vite\bin\vite.js'
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $vite)) {
    throw 'Install Python web dependencies and run npm ci in web first. See docs/WEB.md.'
}
if ($StartModels -and !$Offline) {
    & (Join-Path $PSScriptRoot 'start-local-model.ps1')
    & (Join-Path $PSScriptRoot 'start-local-vision.ps1')
}
$runDir = Join-Path $root 'data\web'
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$config = if ($Offline) { 'configs/offline.toml' } else { 'configs/local-full.toml' }
function Start-OwnedService($name, $exe, $arguments, $port, $workingDir) {
    $recordPath = Join-Path $runDir "$name-process.json"
    if (Test-Path -LiteralPath $recordPath) {
        $saved = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
        $existing = Get-Process -Id $saved.id -ErrorAction SilentlyContinue
        if ($existing -and $existing.Path -eq $saved.path -and
            $existing.StartTime.ToUniversalTime().Ticks -eq $saved.startedTicks) {
            if ($name -eq 'api' -and $saved.config -ne $config) {
                throw 'API uses another config. Run stop-web.ps1 before changing modes.'
            }
            Write-Output "$name already running (PID $($existing.Id))."
            return
        }
    }
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
    try { $listener.Start() } finally { $listener.Stop() }
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $workingDir `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "$name.stdout.log") `
        -RedirectStandardError (Join-Path $runDir "$name.stderr.log")
    @{ id=$process.Id; path=$process.Path; startedTicks=$process.StartTime.ToUniversalTime().Ticks; config=$config } |
        ConvertTo-Json | Set-Content -LiteralPath $recordPath
}
Start-OwnedService 'api' $python @('-m','paintai.api','--config',$config) 8000 $root
Start-OwnedService 'web' $node @(('"'+$vite+'"'),'--host','127.0.0.1') 5173 (Join-Path $root 'web')
foreach ($url in @('http://127.0.0.1:8000/api/v1/health', 'http://127.0.0.1:5173')) {
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { $null=Invoke-WebRequest $url -TimeoutSec 1; $ready=$true; break } catch { Start-Sleep -Milliseconds 500 }
    }
    if (!$ready) { throw "Service did not become ready: $url. Inspect data/web logs." }
}
Write-Output 'Studio: http://127.0.0.1:5173 | API docs: http://127.0.0.1:8000/docs'
