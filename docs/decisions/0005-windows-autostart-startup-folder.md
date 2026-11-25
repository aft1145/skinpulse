# 0005. Windows 自启用启动目录，不用计划任务

- 日期：2026-09-21
- 状态：被 [ADR-0021](0021-windows-autostart-supervisor.md) 取代（2026-11-24）
- 拍板：用户（补记）

## 背景

`scripts/register_windows_task.ps1` 原本注册计划任务（登录时启动、失败自动重启 5 次）。这台机器是 Win11 家庭中文版，非管理员 `Register-ScheduledTask` 报 Access is denied，即使注册的是当前用户自己的任务。

## 决定

用 `-UseStartupFolder`：往用户「启动」文件夹写 `SkinPulse.lnk`，登录时最小化拉起 `skinpulse run`。

## 否决的方案

- 用管理员身份注册计划任务：可行（脚本支持），但当时没走这条路。

## 影响

- **没有失败自动重启**：进程崩了要等下次登录或手动启动。
- Claude 会话里后台拉起的服务会随会话结束退出，所以服务要在用户自己的终端或自启里跑。
- 以后若要失败重启，可考虑 NSSM / WinSW 之类的服务包装器，或管理员注册一次计划任务。
