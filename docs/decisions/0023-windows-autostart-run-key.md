# 0023. Windows 自启改用注册表 Run 键，不用启动目录

- 日期：2026-11-24
- 状态：生效（取代 [ADR-0021](0021-windows-autostart-supervisor.md) 的「登录即起」一条；守护进程那部分仍按 0021）；背景有更正，见文末
- 拍板：用户（接受 intent（未公开），「接受，出 spec」）

## 背景

Windows 真机验收（用户的 Win11 笔记本，家庭中文版 Insider 26300）时，`service install` 写进启动目录的 `SkinPulse.lnk`，注销再登录、重启电脑各一次都没被执行：`supervisor.log` 没有新行，火绒的运行记录里 `.venv\Scripts\pythonw.exe` 也没跑过；手动双击同一个快捷方式却能起来，它也没在「启动应用」里被关掉。

同一次登录里，注册表 `HKCU\...\Run` 下的程序都执行了，Shell-Core 日志里每条都有 9707「Started execution of command」。启动目录的执行不进这个日志，原因查不到；这台机器启动目录里其它几项都被用户关掉了，没有对照。

## 决定

- 登录即起改成 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下的 `SkinPulse` 值：`"<仓库>\.venv\Scripts\pythonw.exe" -m skinpulse supervise --root "<仓库>"`，用标准库 `winreg` 读写。
- Run 键没有工作目录，`supervise` 加 `--root`，启动后先切到仓库根。
- `service status` 读 `Explorer\StartupApproved\Run`，用户在任务管理器「启动应用」里关掉时如实提示；`install` 清掉这个标记；`install`、`uninstall` 顺手删掉旧版的 `SkinPulse.lnk`。
- 守护进程、退出码、防睡眠、「重启服务」都按 ADR-0021 不变。

细节见 [Windows spec](../superpowers/specs/2026-11-24-windows-support.md) §1、§3。

## 否决的方案

- **留在启动目录、先查清原因**：要用户再注销一次做实验；就算查清是这台机器的问题，别人的机器也可能一样，而且出了问题没有日志可查。
- **计划任务（登录触发）**：Win11 家庭版非管理员注册被拒（ADR-0005）。
- **Run 键和启动目录都装上**：靠守护进程的单实例锁去重能行，但两处都要管、`status` 要报两处，出问题时更难说清是哪个起的作用。

## 影响

- 自启有没有被执行，可以在事件查看器 `Microsoft-Windows-Shell-Core/Operational` 里查 9707。
- 一个 Windows 用户只能有一个 `SkinPulse` 自启项：在另一个 clone 里 `install` 会把它改指过去。
- 仓库挪了位置，Run 值指向的目录就失效了；`service status` 会提示重新 `install`。
- 如果以后 Run 键在某些机器上也不执行，再考虑「Run 键 + 计划任务（能注册就注册）」。

## 更正（2026-11-24，同日补记；正文不改）

背景里「启动目录在真机上不执行」不能当结论用。改成 Run 键后，第一次重验同样没起来，查下来是验收方法的问题：`service install` 是在 Claude 会话里跑的，会话写 HKCU 进的是隔离的一份，Explorer、WMI、任务管理器都看不到（会话外用 `StdRegProv` 读不到这个值，会话里却读得到）。由用户在自己的终端里 `service install` 后，注销再登录、重启电脑各一次，服务都被 Explorer 拉起，9707 有记录。

启动目录那次失败的原因仍没查清：当时系统的启动项列表里看得到 `SkinPulse.lnk`，文件是真写进去了。决定不变，Run 键留着：执行有日志可查，`status` 能报出「在启动应用里被关掉」。验收和使用都要求在用户自己的终端里装（CLAUDE.md 坑 16）。
