param([string]$TaskName = "WeChat Intelligence Hub Reminder")
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "已删除计划任务（本地提醒数据库和配置未删除）：$TaskName"
