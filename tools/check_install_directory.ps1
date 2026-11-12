$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$installer = Get-Content -LiteralPath (Join-Path $projectRoot 'build/installer.nsh') -Raw
$package = Get-Content -LiteralPath (Join-Path $projectRoot 'package.json') -Raw | ConvertFrom-Json
$required = @(
    'InstallDir "$LOCALAPPDATA\Programs\${PRODUCT_FILENAME}"',
    'Function IC_NormalizeInstallRoot',
    'StrCpy $INSTDIR "$0\${PRODUCT_FILENAME}"',
    '!macro customInit',
    'Call IC_NormalizeInstallRoot'
)
foreach ($text in $required) {
    if (-not $installer.Contains($text)) { throw "Installer directory configuration missing: $text" }
}
if ($package.build.productName -ne 'Infinite Canvas' -or -not $package.build.nsis.allowToChangeInstallationDirectory) {
    throw 'Installer application folder configuration is invalid.'
}
Write-Output 'Installer application folder static checks passed.'
