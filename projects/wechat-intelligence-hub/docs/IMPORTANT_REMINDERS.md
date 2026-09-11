# 微信重要信息主动提醒（P2）

本模块把 WeChat Intelligence Hub 从“主动查看日报”扩展为本地常驻的行动提醒系统。它复用现有只读 Reader，不发送微信、不点击微信 UI、不修改微信数据库。

## 当前交付范围

### P0：Windows 与增量读取

- 新增 `projects/rion-wechat-reader/rion_wechat_reader_windows.py`，仅负责 Windows 常见微信数据目录发现，数据库读取仍复用现有 Reader。
- Worker 通过 `sessions + tail + context` 增量读取，不全量循环扫描历史。
- 每个会话保存独立 cursor；首次启动只回看最近 120 分钟，避免第一次运行把多年历史全部弹出来。
- Reader 失败不会清空旧游标或把“未读到”解释成“没有消息”。

> Windows 微信版本、数据目录和授权材料存在版本差异。`doctor` 只负责发现和验证，无法读取时会明确失败，不能把通知预览冒充完整历史。

### P1：提醒闭环

规则优先处理：

1. 明确要求本人处理/回复；
2. 明确 Deadline；
3. 本人承诺；
4. 现有商机 `next_follow_up` 已到期；
5. 对方催办；
6. @本人、会议、付款结算和明确运行故障。

状态机：`NEW -> NOTIFIED -> ACKNOWLEDGED/SNOOZED/DONE`，另有 `SUPPRESSED/CANCELLED/EXPIRED`。

普通提醒默认只通知一次；分数达到 `critical` 的提醒在未处理时可按配置重复，默认 30 分钟、最多 3 次。

### P2：本地语义模型

支持 Ollama 或本机 OpenAI-compatible API，用于规则边界项的二次判断。为避免隐私误配置，模型 URL **强制只能解析到 loopback**（`localhost`、`127.0.0.0/8`、`::1`）。远程 URL 会直接拒绝。

规则硬信号不会被模型轻易抹掉；模型只允许在有限范围内调整评分，不能凭空生成日期、金额、身份或承诺。

## 快速开始（Windows）

在项目目录：

```powershell
python .\reminder_cli.py init --project-root ..\..
python .\reminder_cli.py doctor
python .\reminder_cli.py test-notify
python .\reminder_cli.py once
```

确认单次运行正常后安装登录启动任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_reminder_task.ps1
```

卸载常驻任务不会删除本地状态：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\uninstall_reminder_task.ps1
```

## 日常操作

```text
python reminder_cli.py list
python reminder_cli.py ack 12
python reminder_cli.py done 12
python reminder_cli.py snooze 12 --minutes 30
python reminder_cli.py suppress 12
```

默认配置：

- Windows：`%LOCALAPPDATA%\WeChatIntelligenceHub\reminders.json`
- macOS/Linux：`~/.config/wechat-intelligence-hub/reminders.json`

默认提醒状态库和日志都位于本机私有目录，不进入 Git。

## 本地模型

如果本机已安装并运行 Ollama，`init` 会尝试读取 `ollama list` 的第一个已安装模型并启用。也可以设置：

```powershell
$env:WECHAT_REMINDER_MODEL="你的本地模型名"
python .\reminder_cli.py init --force
```

如果模型不可用，规则引擎仍能独立运行；P1 的强规则提醒不依赖模型。

## 手机提醒（可选）

配置文件中 `mobile.provider=ntfy`，填写自建或受控的 ntfy URL 后即可启用。开启手机推送意味着提醒摘要会离开本机，应由使用者自行确认服务部署位置和隐私边界。默认关闭。

## 验收建议

至少连续运行 3 个工作日，人工记录：

- Top-10 precision >= 80%；
- 同一消息不得重复建卡；
- `DONE/SUPPRESSED` 不得再次通知；
- Reader 中断后恢复不能产生历史提醒风暴；
- 微信升级后先通过 Reader/doctor 再恢复常驻任务；
- 本地模型关闭时，五类 P1 核心提醒仍可用。
