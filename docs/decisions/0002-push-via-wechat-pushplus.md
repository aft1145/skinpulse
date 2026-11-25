# 0002. 推送以 PushPlus（微信）为主，ntfy 兜底

- 日期：2026-09-23
- 状态：生效
- 拍板：用户

## 背景

原定 ntfy（R7）。2026-09-23 查实：库里标成 `sent` 的 20 次 ntfy 推送，用户手机上只看到 2 次。原因是机制性的：国内 FCM 不通，ntfy App 要实时收就得常驻后台；ntfy.sh 只存 12 小时。细节见 [SteamDT / PushPlus / ntfy 对接记录 §2、§3](../research/2026-09-23-steamdt-pushplus-ntfy-findings.md)。

用户的要求：通知及时到、通知上的时间准确即可；**不愿意**给手机 App 自启动 / 后台运行权限，不要求锁屏亮屏。

## 决定

- 主通道 PushPlus（微信公众号消息）。微信本身已被系统放行，不需要新权限。
- ntfy 保留，并行发送作兜底；任一通道收下即算发出，每条通道单独记健康状态。
- 每条告警正文末行写「触发于 MM-DD HH:MM」（排名被观察到的时刻）。

## 否决的方案

- 让用户给 ntfy App 开后台 / 自启动 / 电池白名单：用户明确拒绝。

## 影响

- `adapters/pushplus/`、`services/notifier.py`（FanoutNotifier）、`pushplus-token` 命令。
- 「sent」只代表服务端收下，送达以用户在微信里看到为准；PushPlus 真实送达状态的自动核验未做（需开放接口凭证 + IP 白名单）。
