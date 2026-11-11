$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cacheRoot = if ($env:ELECTRON_BUILDER_CACHE) { $env:ELECTRON_BUILDER_CACHE } else { Join-Path $env:LOCALAPPDATA 'electron-builder/Cache' }
$compiler = Get-ChildItem -LiteralPath $cacheRoot -Filter makensis.exe -Recurse |
    Where-Object { $_.FullName -match 'nsis-[^\\]+.*\\Bin\\makensis.exe$' } |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $compiler) { throw 'NSIS compiler was not found after packaging.' }
Push-Location -LiteralPath $projectRoot
try {
    New-Item -ItemType Directory -Path '.test-data' -Force | Out-Null
    & $compiler.FullName /V2 tests/installer-path.nsi
    if ($LASTEXITCODE -ne 0) { throw 'Installer directory check compilation failed.' }
    $checkProcess = Start-Process -FilePath (Join-Path $projectRoot '.test-data/installer-path-test.exe') -WindowStyle Hidden -Wait -PassThru
    if ($checkProcess.ExitCode -ne 0) { throw 'Installer directory checks failed.' }
    Get-Content -LiteralPath '.test-data/installer-path-results.txt'
} finally {
    Pop-Location
}
