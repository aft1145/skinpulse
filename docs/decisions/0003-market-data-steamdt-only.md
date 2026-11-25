# 0003. M2 行情只接 SteamDT

- 日期：2026-09-23
- 状态：生效（是否再接 CSQAQ 见 intent（未公开））
- 拍板：用户（补记，原始讨论未留档；CSQAQ 本身仍待决）

## 背景

原定 SteamDT + CSQAQ 为主（R6）。M2 实现下来，SteamDT 一家已覆盖 M2 的完成标准「比价、走势、指数」：多平台在售/求购价、单品 K 线（时/日/周）、大盘指数与 K 线、饰品目录（且 YOUPIN itemId 就是悠悠 templateId，映射精确）。

## 决定

M2 只用 SteamDT；BUFF / Steam 直连校验也不做（SteamDT 已给出多平台价）。

## 否决的方案

- 同时接 CSQAQ：它独有的只是涨跌榜和 23 个子指数，代价是用户注册、token 绑公网 IP（家宽会变）、1 次/秒、禁商用。

## 影响

- 大盘接口配额很紧（4005），看板行情一律走 `services/market_data.py` 缓存。
- R10 的「涨跌榜」因此未实现；用户想看涨跌榜时再重新考虑 CSQAQ。
