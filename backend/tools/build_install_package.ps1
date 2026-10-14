# ============================================================
# 打包全新安装包脚本（Infinite Canvas）
#
# 生成「纯软件、无个人数据」的完整安装包，供尚未安装的用户
# 首次安装使用。包含：
#   - 全部程序：main.py、static/、workflows/、tools/、tests/、docs/
#   - 运行环境：python/（内嵌 Python，自带依赖）、packages/（离线依赖包）
#   - 即梦 CLI：CLI/ 与 tools/ 中当前工作区已有的相关文件
#   - 启动/安装脚本与说明文档
# 绝不包含个人数据：data/、assets/、history.json、nas_config.json、
#   server_config.cmd（替换为默认）、API/.env（替换为空）等。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_install_package.ps1
#   可选参数：
#     -Version 2026.08.28     # 自定义版本号（默认读取 VERSION 文件）
#     -OutDir D:\发布目录       # 输出目录（默认项目根目录）
# ============================================================
param(
    [string]$Version = "",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Version) {
    $Version = (Get-Content -Path (Join-Path $root "VERSION") -Encoding UTF8 -Raw).Trim()
}
if (-not $OutDir) { $OutDir = $root }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

# ---- 核心文件校验（缺失即失败） ----
foreach ($p in @("main.py", "VERSION", "requirements.txt")) {
    if (-not (Test-Path (Join-Path $root $p))) { throw "缺少核心文件：$p" }
}
foreach ($d in @("static", "workflows", "tools", "python", "packages", "CLI")) {
    if (-not (Test-Path (Join-Path $root $d))) { throw "缺少核心目录：$d/" }
}

# ---- 内容清单 ----
# 工作区软件文件（目录）
$includeDirs = @("static", "workflows", "tools", "python", "packages", "CLI", "tests", "docs")
# 工作区软件文件（根目录）
$includeFiles = @(
    "main.py", "VERSION", "requirements.txt", "get-pip.py",
    "LICENSE", "README.md", ".gitignore",
    "启动服务.bat", "重启服务.bat"
)
# ---- 输出路径 ----
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zipName = "InfiniteCanvas_Full_v${Version}_${stamp}.zip"
$zipPath = Join-Path $OutDir $zipName

# ---- 组装暂存目录 ----
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("ic_install_pkg_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    # 1) 核心目录（用 robocopy 复制大目录，速度快、可处理长路径；排除可再生的字节码缓存）
    foreach ($d in $includeDirs) {
        $src = Join-Path $root $d
        if (-not (Test-Path $src)) { Write-Host "[跳过] 缺少目录 $d/"; continue }
        $excludeDirs = @("__pycache__")
        if ($d -eq "tools") { $excludeDirs += (Join-Path $src "dreamina\home") }
        robocopy $src (Join-Path $staging $d) /E /NJH /NJS /NP /NFL /NDL /R:1 /W:1 /XD $excludeDirs /XF *.pyc *.pyo | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "复制失败：$d/ (robocopy exit=$LASTEXITCODE)" }
        $LASTEXITCODE = 0
    }
    # 2) 根目录文件
    foreach ($f in $includeFiles) {
        $src = Join-Path $root $f
        if (-not (Test-Path $src)) { Write-Host "[跳过] 缺少文件 $f"; continue }
        Copy-Item $src (Join-Path $staging $f)
    }
    # 3) 纯净安装包不携带本机 NAS 配置；首次使用时由用户自行配置。
    # 4) API/ 目录：生成空的 .env（无任何密钥），用户首次使用时自行填写
    New-Item -ItemType Directory -Path (Join-Path $staging "API") | Out-Null
    [System.IO.File]::WriteAllBytes((Join-Path $staging "API\.env"), [byte[]]@())
    # 5) 默认 server_config.cmd（0.0.0.0:3000，无个人 NAS 配置）
    $defaultCfg = "@echo off`r`nset `"RH_SERVER_HOST=0.0.0.0`"`r`nset `"RH_SERVER_PORT=3000`"`r`n"
    [System.IO.File]::WriteAllText((Join-Path $staging "server_config.cmd"), $defaultCfg, (New-Object System.Text.UTF8Encoding($false)))
    # 6) 安装说明.txt
    $readme = @"
Infinite Canvas 全新安装包 v${Version}（${stamp}）
================================================

【这是什么】
完整的 Infinite Canvas 软件（含内嵌 Python 运行时与全部依赖），
不含任何个人数据，适合还没有安装过的用户首次安装。

【安装方法（Windows）】
1. 把本压缩包解压到任意目录（路径建议不要带空格和中文）。
2. 双击「启动服务.bat」。
   - 首次启动会自动打开浏览器，访问 http://127.0.0.1:3000/
   - 局域网内其他设备可访问 http://局域网IP:3000/
3. 完成。无需安装 Python —— 已内置 python\ 运行时和全部依赖包。

【个人数据说明】
本安装包不包含任何个人数据（API 密钥、画布、资产库、历史记录、NAS 配置等）。
首次运行时会自动创建空白数据目录，请在软件界面中自行配置 API。

【目录速览】
  python\     内嵌 Python 运行时（自带全部依赖）
  packages\   离线依赖包
  tools\      本地工具（ffmpeg、Chrome/PS 素材插件、dreamina、即梦 CLI 脚本）
  static\     前端页面
  workflows\  默认工作流
  CLI\        即梦 CLI
"@
    [System.IO.File]::WriteAllText(
        (Join-Path $staging "安装说明.txt"),
        $readme,
        (New-Object System.Text.UTF8Encoding($false))
    )

    # ---- 压缩 ----
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $staging, $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    # ---- 硬校验：禁止任何个人数据路径出现在包内 ----
    $forbiddenPrefixes = @(
        "data/", "assets/", "history.json", "logs/", "output/",
        "_recovery_snapshots/", "upgrade_backup", ".git/",
        "恢复说明.txt", "AGENTS.md", ".env", ".log", "tools/dreamina/home/"
    )
    $bad = @()
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        foreach ($entry in $zip.Entries) {
            $rel = $entry.FullName.TrimEnd('/').Replace('\', '/')
            # 允许安装包自带的空 API/.env（占位，无任何密钥）
            if ($rel -eq "API/.env") { continue }
            # 任意层级的 __pycache__ 目录、任意 .pyc/.pyo 缓存文件都禁止
            $segments = $rel.Split('/')
            if ($segments -contains "__pycache__" -or $rel -like "*.pyc" -or $rel -like "*.pyo") {
                $bad += $rel
                continue
            }
            foreach ($fp in $forbiddenPrefixes) {
                if ($rel.StartsWith($fp, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $bad += $rel
                    break
                }
            }
        }
        $count = $zip.Entries.Count
    } finally {
        $zip.Dispose()
    }
    if ($bad.Count -gt 0) {
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        throw ("安装包校验未通过（已删除）：发现个人数据路径 -> " + ($bad -join ", "))
    }
} finally {
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}

$size = (Get-Item $zipPath).Length
Write-Host ""
Write-Host ("OK  全新安装包已生成：{0}" -f $zipPath)
Write-Host ("    版本 v{0} | 共 {1} 个文件 | {2:N1} MB" -f $Version, $count, ($size / 1MB))
Write-Host "    含 python/ 运行时、packages/ 离线依赖、tools/、CLI/ 等完整软件"
Write-Host "    不含 data/、assets/、history.json、nas_config.json、API/.env 等任何个人数据"
