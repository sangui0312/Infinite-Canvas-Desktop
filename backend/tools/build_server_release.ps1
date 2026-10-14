# Build both public packages and the manifest consumed by the client updater.
param(
    [string]$Version = "",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceVersion = (Get-Content -LiteralPath (Join-Path $root "VERSION") -Encoding UTF8 -Raw).Trim()
if (-not $Version) { $Version = $sourceVersion }
if ($Version -ne $sourceVersion) { throw "-Version 必须与 VERSION 文件一致：$sourceVersion" }
if (-not $OutDir) {
    $OutDir = Join-Path ([System.IO.Path]::GetTempPath()) "InfiniteCanvas_ServerRelease"
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$buildDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ic_server_release_" + [System.Guid]::NewGuid().ToString("N"))
$releaseDir = Join-Path $OutDir "releases"
$staticDir = Join-Path $OutDir "static"
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
New-Item -ItemType Directory -Path $staticDir -Force | Out-Null

try {
    & (Join-Path $PSScriptRoot "build_update_package.ps1") -Version $Version -OutDir $buildDir
    & (Join-Path $PSScriptRoot "build_install_package.ps1") -Version $Version -OutDir $buildDir

    $updateZip = Get-ChildItem -LiteralPath $buildDir -Filter "InfiniteCanvas_Update_v${Version}_*.zip" -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $fullZip = Get-ChildItem -LiteralPath $buildDir -Filter "InfiniteCanvas_Full_v${Version}_*.zip" -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $updateZip -or -not $fullZip) { throw "更新包或完整安装包未生成" }

    $updateTarget = Join-Path $releaseDir $updateZip.Name
    $fullTarget = Join-Path $releaseDir $fullZip.Name
    Copy-Item -LiteralPath $updateZip.FullName -Destination $updateTarget
    Copy-Item -LiteralPath $fullZip.FullName -Destination $fullTarget
    Copy-Item -LiteralPath (Join-Path $root "static\update-notes.json") -Destination (Join-Path $staticDir "update-notes.json") -Force

    $notes = Get-Content -LiteralPath (Join-Path $root "static\update-notes.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$notes.version -ne $Version) { throw "更新公告版本与 VERSION 不一致" }
    $manifest = [ordered]@{
        version = $Version
        update_package = [ordered]@{
            url = "/releases/$($updateZip.Name)"
            size = (Get-Item -LiteralPath $updateTarget).Length
            sha256 = (Get-FileHash -LiteralPath $updateTarget -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        full_package = [ordered]@{
            url = "/releases/$($fullZip.Name)"
            size = (Get-Item -LiteralPath $fullTarget).Length
            sha256 = (Get-FileHash -LiteralPath $fullTarget -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        notes = $notes
    }
    $json = $manifest | ConvertTo-Json -Depth 50
    [System.IO.File]::WriteAllText(
        (Join-Path $OutDir "manifest.json"),
        $json,
        (New-Object System.Text.UTF8Encoding($false))
    )
} finally {
    if (Test-Path -LiteralPath $buildDir) {
        [System.IO.Directory]::Delete($buildDir, $true)
    }
}

Write-Host ""
Write-Host "服务器发布目录已生成：$OutDir"
Write-Host "先将整个发布目录上传到服务器私有临时目录，再运行服务器发布脚本；不要直接覆盖站点根目录。"
Write-Host "服务器命令：python3 /www/backup/site/update-release-manager/publish_release.py publish <服务器私有临时目录>"
