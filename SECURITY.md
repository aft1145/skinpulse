# 安全

## 报告漏洞

请用 GitHub 仓库页的 **Security → Report a vulnerability** 私下报告，不要开公开 issue。会尽量在一周内回复。

特别关心的几类问题：

- token、key、手机号、短信验证码出现在日志、异常消息或 HTTP 响应里；
- 看板的写接口（参数设置、更新悠悠 token、录入 PushPlus token、立即同步、开始选品）能被跨站请求触发；
- 任何会向悠悠发出写请求（下单、改价、撤单）的代码路径。SkinPulse 对悠悠只读。

## 提 issue 前先脱敏

贴日志、截图、`dump` 的原始响应之前，去掉：

- 悠悠 token（`authorization`、`Bearer …`）、`uk`、设备号；
- PushPlus token、ntfy topic、SteamDT key；
- 手机号、求购单号、你的 userId 和昵称。

## 凭据放在哪

凭据存系统钥匙串（macOS Keychain），不写进 `settings.toml`，也不进仓库。`data/` 和 `settings.toml` 已在 `.gitignore` 里。
