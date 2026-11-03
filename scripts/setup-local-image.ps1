$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $root 'data\local-image'
New-Item -ItemType Directory -Force $localDir | Out-Null
$archive = Join-Path $localDir 'sd.zip'
$archiveHash = '10ef877a334c97d014e3e23b4bf6c9077f6b78ff9da3bb157664d2103cb1ae0f'
if (!(Test-Path -LiteralPath $archive) -or
    (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $archiveHash) {
    Invoke-WebRequest `
        'https://github.com/leejet/stable-diffusion.cpp/releases/download/master-1043-3be2378/sd-master-3be2378-bin-win-cpu-x64.zip' `
        -OutFile "$archive.download"
    if ((Get-FileHash -LiteralPath "$archive.download" -Algorithm SHA256).Hash -ne $archiveHash) {
        throw 'Runtime checksum mismatch.'
    }
    Move-Item -LiteralPath "$archive.download" -Destination $archive -Force
}
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $localDir 'runtime') -Force
$model = Join-Path $localDir 'sd_turbo-f16-q8_0.gguf'
$modelHash = 'd50be7655f0a554cf8041c145d88b210bd5f3c545423119dee62ae08cae51580'
if (!(Test-Path -LiteralPath $model) -or
    (Get-FileHash -LiteralPath $model -Algorithm SHA256).Hash -ne $modelHash) {
    Invoke-WebRequest `
        'https://huggingface.co/Green-Sky/SD-Turbo-GGUF/resolve/main/sd_turbo-f16-q8_0.gguf' `
        -OutFile "$model.download"
    if ((Get-FileHash -LiteralPath "$model.download" -Algorithm SHA256).Hash -ne $modelHash) {
        throw 'Model checksum mismatch.'
    }
    Move-Item -LiteralPath "$model.download" -Destination $model -Force
}
Write-Output 'Local image generation ready. Use --config configs/local-image.toml.'
