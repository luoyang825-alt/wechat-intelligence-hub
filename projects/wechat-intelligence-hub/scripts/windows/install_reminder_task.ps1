param(
  [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
  [string]$Python = "python",
  [string]$TaskName = "WeChat Intelligence Hub Reminder",
  [switch]$NoActions,
  [switch]$NoLocalModel
)
$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path $ProjectDir).Path
$RepoRoot = (Resolve-Path (Join-Path $ProjectDir "..\..")).Path
$cli = Join-Path $ProjectDir "reminder_cli.py"
if (-not (Test-Path $cli)) { throw "找不到 reminder_cli.py: $cli" }

$pythonCmd = Get-Command $Python -ErrorAction Stop
$pythonExe = $pythonCmd.Source
$pythonw = Join-Path (Split-Path $pythonExe -Parent) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $pythonExe }

$configDir = Join-Path $env:LOCALAPPDATA "WeChatIntelligenceHub"
$config = Join-Path $configDir "reminders.json"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

$initArgs = @($cli, "--config", $config, "init", "--project-root", $RepoRoot)
if ($NoLocalModel) { $initArgs += "--no-local-model" }
& $pythonExe @initArgs | Out-Host
if ($LASTEXITCODE -ne 0) { throw "提醒配置初始化失败" }

$cfg = Get-Content -Raw -Encoding UTF8 $config | ConvertFrom-Json
$cfg.desktop.enabled = $true
$cfg.desktop.actions_enabled = (-not $NoActions)
$json = $cfg | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($config, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))

function Protect-PrivateFile([string]$Path) {
  if (-not (Test-Path $Path)) { return }
  try {
    & icacls.exe $Path /inheritance:r /grant:r "$env:USERNAME`:(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
  } catch {
    Write-Warning "未能自动收紧 ACL：$Path"
  }
}
Protect-PrivateFile $config
$readerPrivate = @(
  (Join-Path $HOME ".config\rion-wechat-reader\config.json"),
  (Join-Path $HOME ".config\rion-wechat-reader\keys.json")
)
foreach ($privatePath in $readerPrivate) { Protect-PrivateFile $privatePath }

if (-not $NoActions) {
  $protocolRoot = "HKCU:\Software\Classes\wechatreminder"
  New-Item -Force $protocolRoot | Out-Null
  (Get-Item $protocolRoot).SetValue("", "URL:WeChat Reminder Protocol")
  New-ItemProperty -Path $protocolRoot -Name "URL Protocol" -Value "" -PropertyType String -Force | Out-Null
  $commandKey = Join-Path $protocolRoot "shell\open\command"
  New-Item -Force $commandKey | Out-Null
  $handler = "`"$pythonw`" `"$cli`" --config `"$config`" action-uri `"%1`""
  (Get-Item $commandKey).SetValue("", $handler)
}

Write-Host "执行 Reader/提醒状态检查..."
& $pythonExe $cli --config $config doctor | Out-Host
if ($LASTEXITCODE -ne 0) {
  Write-Warning "提醒程序已初始化，但 Reader 尚未 ready；没有注册开机常驻任务。先完成本人微信数据库的合法只读接入，再重新运行本脚本。"
  exit 2
}

Write-Host "发送桌面通知测试..."
& $pythonExe $cli --config $config test-notify | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "桌面通知测试失败；未注册常驻任务。"
}

$arg = '"' + $cli + '" --config "' + $config + '" run'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arg -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Local-first WeChat important information reminder worker" -Force | Out-Null

Write-Host "已完成并验证："
Write-Host "  配置：$config"
Write-Host "  登录启动任务：$TaskName"
if (-not $NoActions) { Write-Host "  Toast 操作：已处理 / 30分钟后 / 已看到" }
Write-Host "可手工验收一次：$pythonExe `"$cli`" --config `"$config`" once"
