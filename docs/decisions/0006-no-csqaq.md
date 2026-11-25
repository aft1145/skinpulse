# 0006. 不接 CSQAQ

- 日期：2026-09-23
- 状态：生效
- 拍板：用户（采纳 intent（未公开） 的建议 A）

## 背景

需求 R6 原定行情主源是 SteamDT + CSQAQ。M2 只接 SteamDT 就达成了完成标准（[ADR-0003](0003-market-data-steamdt-only.md)）。CSQAQ 独有的只有涨跌榜和 23 个子指数。

## 决定

不接 CSQAQ。R10 里的「涨跌榜」不做。

## 否决的方案

- 接 CSQAQ，只用涨跌榜和子指数：要注册账号、token 绑公网 IP（家宽会变，要自动重绑）、1 次/秒、禁商用；换来的功能用户目前不需要。

## 影响

- 不新增 `adapters/csqaq`；大盘页维持 SteamDT 的指数与 K 线。
- 以后想看涨跌榜或分类子指数，或者 SteamDT 变更/收费需要备选源时，再重新考虑。
