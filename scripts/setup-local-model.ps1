$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $root 'data\local-model'
New-Item -ItemType Directory -Force $localDir | Out-Null

function Get-VerifiedFile($Url, $Path, $Sha256) {
    if ((Test-Path -LiteralPath $Path) -and
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Sha256) {
        return
    }
    $partial = "$Path.download"
    Invoke-WebRequest -Uri $Url -OutFile $partial
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Sha256) {
        throw "Checksum mismatch: $partial"
    }
    Move-Item -LiteralPath $partial -Destination $Path -Force
}

$archive = Join-Path $localDir 'llama.zip'
Get-VerifiedFile `
    'https://github.com/ggml-org/llama.cpp/releases/download/b12064/llama-b12064-bin-win-cpu-x64.zip' `
    $archive 'c1ced7358438e19c2af6debefe188280ba39ac0242352719cd30d931ca8eec73'
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $localDir 'runtime') -Force
Get-VerifiedFile `
    'https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf' `
    (Join-Path $localDir 'qwen2.5-0.5b-instruct-q4_k_m.gguf') `
    '74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db'
Write-Output 'Local model ready. Run scripts/start-local-model.ps1.'
