from __future__ import annotations


class SteamDTError(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"steamdt {code}: {msg}")
        self.code = str(code)
        self.msg = msg


class SteamDTAuthError(SteamDTError):
    """API key 缺失、拼错或已被「重新生成」作废。

    服务端对这两种情况的文案有误导性——无效 key 返回的是 **「请先申请ApiKey」**（2007），
    听起来像「你从来没申请过」，实际含义是「这个 key 我不认识」。2026-09-23 实测：把一个
    刚被重新生成掉的旧 key 发过去，拿到的就是这条。照字面读会让人跑去开放平台重新申请，
    而正确的动作是把新 key 填进配置。
    """

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "API key 无效或未配置")


class SteamDTQuotaExceeded(SteamDTError):
    """触到接口配额。各接口额度差得很远（见 client 里每个方法的注释）：
    `/price/batch` 是 1 次/分钟，`/base` 是 1 次/**天**。撞上了就等下一个窗口，别重试。"""


# errorCode -> 异常类型。服务端 **HTTP 永远返回 200**，错误只体现在信封的 errorCode 上，
# 所以任何按状态码判断成败的写法在这里都是错的（这个项目在悠悠那边已经踩过一次同类的坑：
# 429 其实是版本闸门）。
_CODE_MAP: dict[str, type[SteamDTError]] = {
    "2007": SteamDTAuthError,   # 请先申请ApiKey —— 实测是「key 无效」，不是「没申请过」
    "4002": SteamDTAuthError,   # apiKey不能为空
    # 当前接口请求已达到上限。—— 2026-09-23 实测（大盘 K 线几分钟内调了三次就撞上）。
    # 这条文案里用的词是「上限」，下面那串关键词原本一个都没覆盖到：要不是撞上一次真的，
    # 配额错误会被当成普通错误处理。
    "4005": SteamDTQuotaExceeded,
}

# 码表之外的兜底：按 errorMsg/errorCodeStr 里的关键词识别配额类错误。宁可多判一层，也好过
# 把「配额用完」当成普通错误去重试——那只会把下一个配额窗口也烧掉。
_QUOTA_HINTS = ("配额", "频率", "限流", "上限", "quota", "rate limit", "too many")


def raise_for_envelope(body: dict) -> None:
    """按 SteamDT 的统一信封判定成败：success / errorCode / errorMsg。

    `success` 为 true 时直接返回。其余情况一律抛异常——**不要**因为 `data` 恰好非空就放行，
    那正是「守卫没覆盖到业务上不可能为空的字段」这类 bug 的温床。
    """
    if body.get("success") is True:
        return
    code = str(body.get("errorCode") or "")
    msg = str(body.get("errorMsg") or "")
    detail = f"{msg} {body.get('errorCodeStr') or ''}".lower()
    cls = _CODE_MAP.get(code)
    if cls is None and any(h in detail for h in _QUOTA_HINTS):
        cls = SteamDTQuotaExceeded
    raise (cls or SteamDTError)(code or "unknown", msg)
