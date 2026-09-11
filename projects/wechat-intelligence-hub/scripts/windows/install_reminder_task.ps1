param(
  [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
  [string]$Python = "python",
  [string]$TaskName = "WeChat Intelligence Hub Reminder"
)
$ErrorActionPreference = "Stop"
$cli = Join-Path $ProjectDir "reminder_cli.py"
if (-not (Test-Path $cli)) { throw "找不到 reminder_cli.py: $cli" }
& $Python $cli init --project-root (Resolve-Path (Join-Path $ProjectDir "..\..")) | Out-Host
$arg = '"' + $cli + '" run'
$action = New-ScheduledTaskAction -Execute $Python -Argument $arg -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Local-first WeChat important information reminder worker" -Force | Out-Null
Write-Host "已安装登录启动任务：$TaskName"
Write-Host "先运行：$Python $cli doctor"
Write-Host "再运行：$Python $cli test-notify"
