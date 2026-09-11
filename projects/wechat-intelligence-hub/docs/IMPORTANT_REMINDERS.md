# 微信重要信息主动提醒（P2）

本模块把 WeChat Intelligence Hub 从“主动查看日报”扩展为本地常驻的行动提醒系统。它复用现有只读 Reader，不发送微信、不点击微信 UI、不修改微信数据库。

## 当前交付范围

### P0：Windows 与增量读取

- `projects/rion-wechat-reader/rion_wechat_reader_windows.py` 负责 Windows 常见微信数据目录发现，并使用 NTFS ACL 语义校验私有 Reader 配置/访问材料；数据库读取仍复用现有 Reader。
- Worker 通过 `sessions + tail + context` 增量读取，不全量循环扫描历史。
- 每个会话保存独立 cursor 与 `last_seen_time`；首次启动只回看最近 120 分钟，避免第一次运行把多年历史全部弹出来。
- 如果微信升级或消息库轮换后 local ID 重置，系统发现“会话时间已经更新、旧 cursor 却读不到新消息”时，会自动按 `last_seen_time` 用 timeline 恢复，再重建 cursor。
- Reader 失败不会清空旧游标，也不会把“未读到”解释成“没有消息”；连续失败会进入健康提醒。

> Windows 微信版本、数据目录和授权材料存在版本差异。`doctor` 只负责发现和验证，无法读取时会明确失败，不能把通知预览冒充完整历史。Windows wrapper 不获取 key、不扫描微信进程、不注入或 Hook 微信。

### P1：提醒闭环

规则优先处理：

1. 明确要求本人处理/回复；
2. 明确 Deadline；
3. 本人承诺；
4. 现有商机 `next_follow_up` 已到期；
5. 对方催办；
6. @本人、会议、付款结算和明确运行故障。

状态机：`NEW -> NOTIFIED -> ACKNOWLEDGED/SNOOZED/DONE`，另有 `SUPPRESSED/CANCELLED/EXPIRED`。

同一提醒 key 严格幂等；同一个仍处于开放状态的关联提醒会更新原卡片，而不是反复创建新卡片。普通提醒默认只通知一次；分数达到 `critical` 的提醒在未处理时可按配置重复，默认 30 分钟、最多 3 次。

Windows Toast 支持“已处理 / 30分钟后 / 已看到”。按钮通过本机 `wechatreminder://` 协议调用 `reminder_cli.py action-uri`，只更新本地提醒状态，不会发送微信。

如果当前 Windows 环境不支持 unpackaged WinRT Toast，通知器会降级到系统托盘气泡；降级通知仍可见，但不带交互按钮。

### P2：本地语义模型

支持 Ollama 或本机 OpenAI-compatible API，用于规则边界项的二次判断。为避免隐私误配置，模型 URL **强制只能使用字面 loopback**（`localhost`、`127.0.0.0/8`、`::1`）。远程 URL 和普通主机名会直接拒绝。

规则硬信号不会被模型轻易抹掉；模型只允许在有限范围内调整评分，不能凭空生成日期、金额、身份或承诺。没有本地模型时，P1 规则引擎仍能独立运行。

## Windows 推荐安装流程

在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_reminder_task.ps1
```

安装脚本按以下顺序执行：

1. 创建 `%LOCALAPPDATA%\WeChatIntelligenceHub\reminders.json`；
2. 用无 BOM UTF-8 写回配置，避免 Windows PowerShell 5 与 Python JSON 读取不兼容；
3. 收紧提醒配置，以及默认位置下已有 Reader `config.json/keys.json` 的 NTFS ACL；
4. 注册 `wechatreminder://` 本地协议；
5. 运行 `doctor`；
6. 只有 Reader、状态库和本地模型隐私边界通过后，才执行桌面通知测试；
7. 只有通知测试也通过后，才注册登录启动计划任务。

如果 Reader 尚未 ready，脚本会保留本地配置并退出，但**不会**注册开机常驻任务。完成本人微信数据库的合法只读接入后，再重新运行安装脚本即可。

不需要 Toast 操作按钮时：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_reminder_task.ps1 -NoActions
```

不需要自动检测/启用本地 Ollama 模型时：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_reminder_task.ps1 -NoLocalModel
```

卸载常驻任务和本地 URI 协议，不删除提醒状态库和配置：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\uninstall_reminder_task.ps1
```

## 手工验收与日常操作

```powershell
python .\reminder_cli.py init --project-root ..\..
python .\reminder_cli.py doctor
python .\reminder_cli.py windows-discover
python .\reminder_cli.py test-notify
python .\reminder_cli.py once
python .\reminder_cli.py list
python .\reminder_cli.py ack 12
python .\reminder_cli.py done 12
python .\reminder_cli.py snooze 12 --minutes 30
python .\reminder_cli.py suppress 12
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

配置文件中 `mobile.provider=ntfy`，填写自建或受控的 ntfy URL 后即可启用。JSON 使用 UTF-8 发送，可选择在正文中包含聊天名称。开启手机推送意味着提醒标题/摘要会离开本机，应由使用者自行确认服务部署位置和隐私边界。默认关闭；隐私优先时建议使用自建内网 ntfy。

## 使用验收标准

先单次验证，再启用常驻；建议至少连续运行 3 个工作日，人工记录：

- Top-10 precision >= 80%；
- 同一消息不得重复建卡，同一开放关联事项不得形成提醒洪水；
- `DONE/SUPPRESSED` 不得再次通知；
- 普通 `NOTIFIED` 项不得因空 `next_notify_at` 被循环扫描；
- Reader 中断后恢复不能产生历史提醒风暴；
- 微信消息库轮换/local ID 重置后能够从 `last_seen_time` 恢复并重建 cursor；
- 微信升级后先通过 Reader/doctor 再恢复常驻任务；
- Toast 操作按钮确实能把状态改为 `DONE/ACKNOWLEDGED/SNOOZED`；
- 本地模型关闭时，P1 核心提醒仍可用；
- 将本地模型 URL 故意改成远程地址时，`doctor` 必须失败，微信上下文不得发出。

## 安全边界

- Reader 继续只读微信，提醒子系统只写自己的 SQLite、日志和私有配置。
- Windows Reader 使用 NTFS ACL 判断访问材料是否过宽，不用 POSIX `0600` 直接套用到 Windows。
- 本地模型只允许 loopback；手机推送默认关闭。
- 通知按钮只更新提醒状态，不发送微信、不修改联系人、不发起支付。
- 不要把真实聊天、提醒状态库、Reader key/config、日志或生成报告提交到 Git。
