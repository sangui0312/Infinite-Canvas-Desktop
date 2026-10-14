param([switch]$InstallDependencies)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$previousOptions = $env:NODE_OPTIONS
Push-Location -LiteralPath $projectRoot
try {
    # Honor the Windows system trust store; never disable TLS verification.
    $env:NODE_OPTIONS = (($previousOptions, '--use-system-ca') | Where-Object { $_ }) -join ' '
    & python -B -X utf8 tools/prepare_runtime.py
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Windows runtime preparation failed.' }
    if ($InstallDependencies) {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed.' }
        & node node_modules/electron/install.js
        if ($LASTEXITCODE -ne 0) { throw 'Electron runtime download failed.' }
    }
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw 'Shell checks failed.' }
    & backend/python/python.exe -B -X utf8 -m unittest discover -s tests -p test_desktop_backend.py
    if ($LASTEXITCODE -ne 0) { throw 'Backend checks failed.' }
    & npm.cmd run dist:win
    if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed.' }
    & node tools/write_update_notes.cjs
    if ($LASTEXITCODE -ne 0) { throw 'Desktop update announcement validation failed.' }
    & backend/python/python.exe -B -X utf8 tools/verify_payload.py
    if ($LASTEXITCODE -ne 0) { throw 'Payload checks failed.' }
    & node tools/run_smoke.cjs --packaged
    if ($LASTEXITCODE -ne 0) { throw 'Packaged app checks failed.' }
    & node tools/check_media.cjs
    if ($LASTEXITCODE -ne 0) { throw 'Media checks failed.' }
} finally {
    $env:NODE_OPTIONS = $previousOptions
    Pop-Location
}
