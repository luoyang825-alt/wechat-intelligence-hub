param([string]$TaskName = "WeChat Intelligence Hub Reminder")
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Remove-Item -Path "HKCU:\Software\Classes\wechatreminder" -Recurse -Force
Write-Host "已删除计划任务和 wechatreminder:// 协议；本地提醒数据库、配置和微信数据均未删除：$TaskName"
