# 0021. Windows 自启：启动目录 + Python 守护进程

- 日期：2026-11-24
- 状态：生效（取代 [ADR-0005](0005-windows-autostart-startup-folder.md)）；「登录即起」一条被 [ADR-0023](0023-windows-autostart-run-key.md) 取代（2026-11-24）
- 拍板：用户（接受 intent（未公开），「按建议」）

## 背景

开源后别人多半用 Windows。以前在 Windows 上跑时，自启走启动目录快捷方式，没有失败重启（ADR-0005），监控停摆过好几次；计划任务在 Win11 家庭版上非管理员注册会被拒。macOS 上的做法（LaunchAgent + `caffeinate -s`，[ADR-0010](0010-macos-launchagent-caffeinate-on-ac.md)）不能照搬。

## 决定

- **登录即起**：启动目录里的 `SkinPulse.lnk`，目标是 `.venv\Scripts\pythonw.exe -m skinpulse supervise`（没有控制台窗口）。由 `skinpulse service install` 写入，不需要管理员。
- **崩溃重启**：`skinpulse supervise` 是一个 Python 守护进程，拉起 `skinpulse run`（环境变量 `SKINPULSE_SUPERVISOR=windows`），按退出码决定下一步：
  - 退出码 75 → 2 秒后重拉（看板「重启服务」）；
  - 有 `data/skinpulse.stop` → 守护进程退出（`service stop`）；
  - 其它退出码 → 30 秒后重拉；10 分钟内崩 10 次，改成 5 分钟一次。
- **防睡眠**：守护进程主线程里每 60 秒查一次电源。接电源时 `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`，用电池时撤掉，口径和 `caffeinate -s` 一致。
- **停止**：`service stop` 直接结束服务进程（TerminateProcess），不做优雅退出。macOS 上 SIGTERM 之后也是 `os._exit`、不等当前一轮；在途通知由下次启动的 `recover_pending` 收尾。
- 管理命令 `skinpulse service install | status | stop | start | restart | uninstall` 用 Python 写，可以在 macOS 上单测。旧的 `scripts/register_windows_task.ps1` 删掉。

## 否决的方案

- **计划任务（登录触发 + 失败重启）**：有些机器要管理员才能注册（ADR-0005 就卡在这）。守护进程已经负责崩溃重启，计划任务只剩「登录时启动」一个作用，启动目录也能做到。
- **Windows 服务（NSSM / WinSW）**：要管理员、要额外下载 exe；以服务身份跑，读不到用户凭据管理器里的 token。
- **PowerShell 写守护循环**：没法在 macOS 上测，退出码、日志、单实例锁也没法和现有 Python 代码共用。

## 影响

- 系统页「重启服务」在两个平台上都能用：macOS 给自己发 SIGTERM，Windows 以 75 退出。
- 「不登录也运行」不支持：那需要存 Windows 密码或用服务身份。
- 守护进程本身崩了没人拉起（它很小，只做拉进程和查电源）。下次登录会恢复，`service status` 能看到。
- CI 加了 `windows-latest` job，Windows 专属的测试只在那里真的跑。
- 这条只影响别人在 Windows 上怎么跑；本项目自己的生产实例仍在 macOS 上（[ADR-0009](0009-run-service-on-macos.md)）。
