#Requires -Version 5.1
<#
.SYNOPSIS
  Infinite Canvas 开机自启动管理脚本（任务计划程序方式，方案 A）

.DESCRIPTION
  enable  - 注册「登录触发」的自启动计划任务（登录后延迟 30 秒，隐藏窗口启动服务）
  disable - 注销自启动计划任务
  status  - 查看自启动任务状态

  任务名：InfiniteCanvasAutoStart
  启动方式：wscript 静默运行 启动服务-静默.vbs（无控制台窗口，先加载 server_config.cmd 再启动 main.py）

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 enable
  powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 disable
  powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 status
#>
param(
    [ValidateSet("enable", "disable", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$taskName = "InfiniteCanvasAutoStart"
$root = Split-Path $PSScriptRoot -Parent          # 项目根目录（tools\ 的上一级）
$vbs = Join-Path $root "启动服务-静默.vbs"
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"

if (-not (Test-Path $vbs)) {
    throw "未找到静默启动脚本：$vbs"
}
if (-not (Test-Path $wscript)) {
    throw "未找到 wscript.exe：$wscript"
}

switch ($Action) {
    "enable" {
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "已存在同名任务，先注销旧任务..." -ForegroundColor Yellow
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $trigger.Delay = "PT30S"   # 登录后延迟 30 秒启动，避开开机负载高峰
        $taskAction = New-ScheduledTaskAction -Execute $wscript `
            -Argument ('"{0}"' -f $vbs) `
            -WorkingDirectory $root
        # ExecutionTimeLimit = 0 表示不限制运行时长（服务常驻）
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $taskAction `
            -Settings $settings `
            -Description "Infinite Canvas 服务开机自启动（登录触发，延迟30秒，隐藏窗口）" | Out-Null
        Write-Host "[OK] 已开启开机自启动（任务：$taskName）" -ForegroundColor Green
        Write-Host "下次登录系统后约 30 秒自动启动服务：http://127.0.0.1:3000/"
        Write-Host "查看状态：powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 status"
    }
    "disable" {
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            Write-Host "[OK] 已关闭开机自启动（任务：$taskName 已注销）" -ForegroundColor Green
        } else {
            Write-Host "开机自启动本来就没开启（任务：$taskName 不存在）"
        }
    }
    "status" {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host "状态：未开启开机自启动"
            Write-Host "开启方法：powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 enable"
        } else {
            $info = Get-ScheduledTaskInfo -TaskName $taskName
            $trig = $task.Triggers | Select-Object -First 1
            Write-Host "状态：已开启（任务：$taskName，当前：$($task.State)）"
            Write-Host ("触发器：登录时（用户 {0}，延迟 {1}）" -f $trig.UserId, $trig.Delay)
            Write-Host ("上次运行：{0}  上次结果：{1}" -f $info.LastRunTime, $info.LastTaskResult)
            Write-Host ("下次运行：{0}" -f $info.NextRunTime)
            Write-Host "关闭方法：powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 disable"
        }
    }
}
