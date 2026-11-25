# 0011. HTTP 客户端忽略环境里的代理变量，全部直连

- 日期：2026-09-24
- 状态：生效
- 拍板：用户认可 Claude 的建议

## 背景

在 Mac 上接手时，用户的 `~/.zshrc` 检测到 Clash（127.0.0.1:7890）在跑，就会自动 `proxyon`，其中包括 `ALL_PROXY=socks5://…`。httpx 默认 `trust_env=True`，读到 socks5 代理就要求装 `socksio`，结果每条联网命令都在构造客户端时报 `ImportError`。每开一个新终端标签都要先 `proxyoff`。

即使装了 `socksio`，行为也不一致：从终端启动的服务会走代理，由 launchd 拉起的（[ADR-0010](0010-macos-launchagent-caffeinate-on-ac.md)）不读 `.zshrc`，所以是直连。

## 决定

四个 HTTP 客户端（悠悠、SteamDT、PushPlus、ntfy）默认构造时一律 `trust_env=False`：不读 `HTTP(S)_PROXY` / `ALL_PROXY` / `NO_PROXY`，也不读 `SSL_CERT_FILE` 等环境变量，**全部直连**。

## 否决的方案

- **依赖里加 `httpx[socks]`、照旧读环境变量**：终端和 launchd 两种启动方式的网络行为不一样，出了问题不好排查。悠悠请求还可能从海外节点出去，更容易被风控。

## 影响

- 悠悠、PushPlus、SteamDT 都是国内服务，直连正合适。
- ntfy（ntfy.sh 在海外）目前没配置。以后启用时如果直连不通，再给 `[ntfy]` 单独加一个显式的代理配置，**不要**改回读环境变量。
- 测试里注入的 `http=` 客户端不受影响。
