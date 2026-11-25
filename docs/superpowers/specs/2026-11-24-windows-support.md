# Windows 支持

- 日期：2026-11-24
- 来源：intent（未公开）（接受）
- 状态：定稿（2026-11-24：私有仓库也加 CI；真机用用户的 Win11 笔记本）
- 修订：2026-11-24，真机验收时启动目录的快捷方式登录后没被执行，自启改用注册表 Run 键（intent（未公开），[ADR-0023](../../decisions/0023-windows-autostart-run-key.md)）。改的是 §1、§2「`--root`」、§3 的 `install` / `status` / `uninstall`、§5、§6、§7；守护进程、退出码、防睡眠、重启按钮不变。

macOS 上的做法（LaunchAgent + `caffeinate`，[ADR-0010](../../decisions/0010-macos-launchagent-caffeinate-on-ac.md)）一点不变。本 spec 只给 Windows 10/11 补上同样的三件事：**登录即起、崩了自动拉起、接电源时不睡**，以及一键重启和 CI。

## 1. 总体做法：Python 守护进程 + 注册表 Run 键

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
  SkinPulse = "<仓库>\.venv\Scripts\pythonw.exe" -m skinpulse supervise --root "<仓库>"
        │ 登录时由 Explorer 执行
        ▼
  pythonw.exe -m skinpulse supervise --root <仓库>   （先切到 <仓库>）
        │  每 60 秒：接电源 → SetThreadExecutionState 防睡眠；用电池 → 撤掉
        │  拉起子进程，等它退出，按退出码决定什么时候再拉
        ▼
  pythonw.exe -m skinpulse run --console-level WARNING
  （环境变量 SKINPULSE_SUPERVISOR=windows）
```

- **为什么是 Run 键、不是启动目录**（修订）：
  - 真机验收时，启动目录里的 `SkinPulse.lnk` 在注销再登录、重启电脑后都没被执行，手动双击却能起来。原因没查到，而且启动目录执行没执行，系统日志里不记。
  - 同一次登录里，Run 键的程序都执行了，Shell-Core 日志（`Microsoft-Windows-Shell-Core/Operational`）里每条都有 9707「Started execution of command」，事后查得到。
  - Run 键同样不要管理员；用户在任务管理器「启动应用」里看得到、关得掉；用标准库 `winreg` 写，不用再调 PowerShell。
- **为什么不用计划任务**：intent 里写的是「能注册计划任务就用」。但崩溃重启已经由守护进程做了，计划任务只剩「登录时启动」这一个作用，Run 键也能做到，而且永远不需要管理员（[ADR-0005](../../decisions/0005-windows-autostart-startup-folder.md) 的坑）。所以只走 Run 键，少一条分支、少一套测试。
- **为什么不用 Windows 服务**：要管理员，要额外的 exe；以服务身份跑读不到用户凭据管理器里的 token。
- **为什么守护进程用 Python、不用 PowerShell**：能在 Mac 上写单元测试；退出码、日志、单实例锁和现有代码共用。PowerShell 只在安装时用来创建 `.lnk`。
- 用 `pythonw.exe`：没有控制台窗口，登录后桌面上不会多一个黑框。

## 2. `skinpulse supervise`（新命令）

新模块 `skinpulse/supervise.py`，逻辑写成纯函数和可替换的依赖（启动子进程、睡眠、时钟、电源状态），单测在任何平台都能跑。

**`--root DIR`**（修订）：Run 键没有「起始位置」，Explorer 拉起的进程工作目录不是仓库根。`settings.toml`、`data/` 都按工作目录解析，所以 `supervise` 第一件事是 `os.chdir(DIR)`，然后才 `load_settings`；子进程照旧用 `Path.cwd()`，自然也在仓库根。
- 不给 `--root` 时行为不变（用当前目录），手动在仓库根跑 `supervise` 仍然可以。
- `DIR` 不存在：退出 2（这时还不知道日志该写哪，没处可写；`service status` 会把这种情况查出来，见 §3）。
- `skinpulse` 是以可编辑方式装进 `.venv` 的（`_editable_impl_skinpulse.pth`），`-m skinpulse` 不依赖工作目录就能导入。

**单实例**：守护进程自己拿一个锁 `data/skinpulse.supervise.lock`（复用 `runlock.acquire_run_lock`）。拿不到就说明已经有一个守护进程，直接退出 1。子进程照旧拿 `skinpulse.run.lock`，手动在终端里跑的 `run` 和守护的那个撞车时，后来的那个退出（现有行为）。

**退出码和下一步**：

| 子进程退出码 | 意思 | 守护进程怎么做 |
|---|---|---|
| `75`（`RESTART_EXIT`） | 系统页点了「重启服务」 | 2 秒后重拉 |
| 有 `data/skinpulse.stop` 文件 | 用户执行了 `service stop` | 删掉 stop 文件，守护进程退出 |
| 其它任何退出码 | 崩了，或者撞上单实例锁 | 30 秒后重拉（和 launchd 的 `ThrottleInterval` 一样） |

- **重拉过快保护**：10 分钟内连续崩 10 次，间隔改成 5 分钟，并在日志里写一行警告（launchd 没这个，但 Windows 上没有 launchd 那样的节流兜底）。正常跑满 10 分钟后计数清零。
- **日志**：子进程的 stdout / stderr 追加到 `data/logs/supervisor.log`（相当于 Mac 上的 `launchd.log`：`--console-level WARNING`，只有警告、报错和 traceback）。守护进程自己的事件（拉起、退出码、电源状态变化）也写进这个文件，每行带时间。文件超过 5 MB 时滚动一次（留 1 份旧的）。
- **子进程的命令**：`[sys.executable, "-m", "skinpulse", "run", "--console-level", "WARNING"]`，工作目录 = 仓库根目录，环境变量加 `SKINPULSE_SUPERVISOR=windows`。

**防睡眠**（只在 Windows 上；其它平台是空操作）：
- 每 60 秒用 `GetSystemPowerStatus` 查一次 `ACLineStatus`：
  - 接电源 → `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`，和 `caffeinate -s` 一样只拦系统睡眠，不拦关屏。
  - 用电池或状态未知 → `SetThreadExecutionState(ES_CONTINUOUS)` 撤掉。
- 这个状态挂在调用它的线程上，所以一直在守护进程的主线程里调用；守护进程退出，状态自然撤掉。
- 合盖：Windows 默认「合盖睡眠」会无视这个状态（和 Mac 一样），使用手册里写清楚。

## 3. `skinpulse service ...`（新命令组，只在 Windows 上可用）

对应 Mac 上的 `scripts/macos/launchagent.sh`。用 Python 写，不另写 PowerShell 脚本；在非 Windows 上执行时提示「macOS 用 scripts/macos/launchagent.sh」并退出 1。

| 子命令 | 做什么 |
|---|---|
| `install` | 检查 `.venv\Scripts\pythonw.exe` 和 `settings.toml` 存在；写 Run 值（见下）；清掉「启动应用」里的关闭标记；删掉旧版留下的 `SkinPulse.lnk`；然后立刻拉起一个守护进程（同样带 `--root`，不用等下次登录）。重复执行无害。 |
| `status` | 开机自启的状态（见下）；守护进程、服务进程在不在跑（各自锁文件里的 pid）；`supervisor.log` 最后 20 行。 |
| `stop` | 写 `data/skinpulse.stop`，结束服务进程（锁文件里的 pid，`TerminateProcess`）；等守护进程退出（最多 10 秒）。下次登录会自动恢复。 |
| `start` | `stop` 之后重新拉起守护进程。 |
| `restart` | `stop` + `start`。拉了新代码、`uv sync` 之后用。 |
| `uninstall` | 先删掉 Run 值、「启动应用」里的标记、旧版 `SkinPulse.lnk`（有哪个删哪个），再 `stop`：停不下来时下次登录也不会再起来。写注册表被拒时报「改不了注册表」，不甩 traceback。 |

**Run 值**（修订，取代原来的 `.lnk`）：
- 位置 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值名 `SkinPulse`，类型 `REG_SZ`，用标准库 `winreg` 读写（不再调 PowerShell）。
- 内容：`"<仓库>\.venv\Scripts\pythonw.exe" -m skinpulse supervise --root "<仓库>"`。两个路径**总是**加双引号；按 `CommandLineToArgvW` 的规则，紧挨着结尾引号的反斜杠要成对（仓库在盘符根 `D:\` 时会碰到）。Windows 路径里不会有双引号；空格、`&`、`'`、中文都原样放在引号里（Explorer 用 CreateProcess 执行，不经过 cmd）。拼命令行写成纯函数，测试覆盖这几种路径，并用解析回来的结果断言。
- 一个用户只有一个 `SkinPulse` 值：在另一个 clone 里 `install` 会把它改指过去，输出里说一句「原来指向 X，已改成这里」。

**「启动应用」里的关闭标记**：用户在任务管理器「启动应用」或设置里关掉自启，Windows 会在 `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run` 下写同名的 `REG_BINARY` 值，第一个字节是奇数（`03`、`07`）表示关掉，偶数（`02`、`06`）或没有这个值表示开着。`install` 是用户明确要开，所以删掉这个值；`status` 只读。

**`status` 第一行**按情况写（每种都有单测）：

| 情况 | 输出 |
|---|---|
| 没有 Run 值 | `开机自启：没装` |
| 有，指向当前仓库，没被关 | `开机自启：已安装（注册表 Run 键）`，下一行是完整命令 |
| 被用户在「启动应用」里关了 | `开机自启：已安装，但在任务管理器「启动应用」里被关掉了：在那里打开，或重新 service install` |
| 指向别的目录 | `开机自启：指向另一个目录 X（当前是 Y）：要用这里的就在这里 service install` |
| 指向的目录或 `pythonw.exe`、`settings.toml` 不在了 | `开机自启：已安装，但 X 不在了，登录后起不来：重新 service install` |
| 值不是 `install` 拼的格式（别人写的、手改过） | `开机自启：注册表里的 SkinPulse 项认不出来（…）：重新 service install` |
| 旧版 `SkinPulse.lnk` 还在 | 另起一行：`旧版快捷方式还在：<路径>（重新 service install 会删掉）` |
- 拉起守护进程用 `subprocess.Popen(..., creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`，关掉执行 `install` 的终端不影响它。
- **`stop` 直接结束进程，不做优雅退出**。Mac 上 SIGTERM 之后也是 `os._exit`，不等正在跑的那一轮（`cli.py` 里的注释）；在途的 pending 通知由下次启动时的 `recover_pending` 收尾，SQLite 是 WAL 模式，进程被杀不会损坏。所以 Windows 上不用为了优雅退出去折腾控制台信号（`pythonw` 没有控制台，`CTRL_BREAK_EVENT` 发不进去）。

## 4. 重启按钮和系统页

- `web/system.py`：`_supervised()` 认 `launchd` 和 `windows` 两个值。
- `request_restart` 的 `kill`：
  - macOS：不变（给自己发 SIGTERM，launchd 拉起）。
  - Windows：0.5 秒后先刷日志，再 `os._exit(RESTART_EXIT)`，守护进程看到 75 就 2 秒后重拉。
- 拒绝时的提示按平台写：Mac 写「不是由 LaunchAgent 拉起的」，Windows 写「不是由开机自启（skinpulse service install）拉起的」。
- `run` 撞上单实例锁时的提示也按平台写：Windows 写 `skinpulse service stop`。
- `SystemPage.vue` 运行方式卡片：把 `supervisor === 'launchd'` 换成「有 supervisor」。
  - 文案都是「登录后自动启动 / 崩溃 30 秒内拉起」和「防睡眠：接电源时生效 / 合盖或用电池仍会睡」。
  - 底部那句取消自启的命令按 supervisor 显示：launchd 显示 `scripts/macos/launchagent.sh uninstall`，windows 显示 `uv run python -m skinpulse service uninstall`。
  - 前端改完要重新构建 `dist` 并提交。

## 5. CI

`.github/workflows/ci.yml` 加一个 job：

```yaml
python-windows:
  runs-on: windows-latest
  env: { PYTHON_KEYRING_BACKEND: keyring.backends.null.Keyring }
  steps: checkout → setup-uv → uv sync --frozen → uv run pytest -q
```

- 前端 job 不在 Windows 上重复跑：产物是同一份，`test_frontend_build` 已经覆盖了 CRLF 检出。
- 现有在 Windows 上被跳过的测试（POSIX 信号、plutil）保持跳过。新增几条**只在 Windows 上跑**的测试：
  - `supervise` 用一个假子进程脚本，跑通「退出 75 → 重拉」和「stop 文件 → 退出」；
  - `SetThreadExecutionState` 和 `GetSystemPowerStatus` 能真的调到（只断言返回值非 0）；
  - ~~`.lnk` 能创建在临时目录里，再读回目标和参数~~（修订：不再建 `.lnk`，这条删掉）；
  - 真的在 `HKCU\Software\SkinPulseTest\...` 下写一个 Run 值和一个 StartupApproved 值、读回来、再删掉（不碰真的 Run 键）。注册表的位置做成可注入的参数，macOS 上用字典替身测同一套逻辑。
- **CI 只有一份，放在仓库根 `.github/workflows/ci.yml`**（从 `public/` 挪过来）：私有仓库直接用，导出时原样带上：Windows 测试在私有仓库推送时就能跑，不用为了看 CI 提前公开代码。私有仓库 Actions 免费额度每月 2000 分钟，Windows 按 2 倍计费。

## 6. 文档

- `docs/guide.md` 新增「在 Windows 上运行」一节：
  - 安装 uv；凭据用 `--from-file`（隐藏输入框粘贴会混进控制字符）；
  - `service install / status / stop / restart / uninstall`；
  - 合盖睡眠的说明；日志在哪；
  - 换 token 和 Mac 一样走看板。
- README：平台说明改成「macOS、Windows 10/11」，快速开始第 3 步分平台写。
- 新 ADR-0021「Windows 自启：启动目录 + Python 守护进程」，ADR-0005 标「被 0021 取代」。ADR-0009 不动（它说的是你自己的生产环境放在 Mac）。
- 公开版 `CLAUDE.md` 的「运维」一节加一行 Windows 命令。
- （修订）凡是写「启动目录 / 快捷方式」的地方改成「注册表 Run 键」：`docs/guide.md`「在 Windows 上运行」（含「在任务管理器『启动应用』里能看到、能关；关了 `service status` 会提示」）、README 快速开始那行注释、`CLAUDE.md` 运维那行、`service install` 的命令说明、`service_win.py` / `supervise.py` / `cli.py` 的文档字符串。
- （修订）新 ADR-0023「Windows 自启改用注册表 Run 键」；ADR-0021 状态改成「部分被 0023 取代（登录即起的方式）」，守护进程那部分照旧生效；ADR 索引同步。
- `CLAUDE.md`、`public/CLAUDE.md` 的坑不加新编号，除非实现中撞上新坑。

## 7. 验证

1. Mac 上：pytest 全绿；不在 Windows 上的测试用替身覆盖 `supervise` 的状态机（退出码表格的每一行、过快重拉保护、日志滚动）和 `service` 的参数检查。
2. 私有仓库推送后看 `python-windows` job 变绿。
3. 真机（用户的 Win11 笔记本）：跑 `demo`，然后跑 `service install`，退出登录再登录，确认服务起来；结束服务进程，确认 30 秒内拉起；点「重启服务」。**全程不录真凭据**，或者先停掉 Mac 上的正式服务（单实例）。
   - 2026-11-24 第一次验收：除了「登录后起来」都过了，结果记在 plan「收尾」。
   - **`service` 命令一律由用户在自己的终端里跑**：Claude 会话写的注册表系统看不到（CLAUDE.md 坑 16），第一次验收和第一次重验都栽在这上面。会话这边只做只读检查，真实注册表用 WMI `StdRegProv` 读。9707 里记的是命令最后一个反斜杠后面的部分（仓库目录名），不是 `pythonw.exe`。
   - 修订后重验（测试 clone `D:\Ning\Software\AI_Tools\SkinPulse-wintest`，没有凭据）：
     - 在旧版装过的状态下 `git pull`、`uv sync`、`service install`：旧 `SkinPulse.lnk` 被删掉，Run 值写上；
     - **注销再登录**、**重启电脑**各一次，`service status` 两项都在跑；Shell-Core 日志里有 `pythonw.exe` 的 9707；
     - 在任务管理器「启动应用」里关掉 SkinPulse，`service status` 提示「被关掉了」；再 `service install` 恢复；
     - `service uninstall` 后 Run 值、StartupApproved 值、进程都没了。

## 8. 不做

- 不做 Windows 安装包、托盘图标。
- 不支持「不登录也运行」（那需要存储 Windows 密码或用服务身份，读不到用户凭据）。
- 不改 Mac 的自启方式。
