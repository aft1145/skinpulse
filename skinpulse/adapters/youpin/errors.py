from __future__ import annotations


class YoupinError(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"youpin {code}: {msg}")
        self.code = str(code)
        self.msg = msg


class LoginRequired(YoupinError): ...
class RiskControl(YoupinError): ...
class VersionOrUkRejected(YoupinError): ...
class UkExpired(YoupinError): ...
class RateLimited(YoupinError): ...
class MachineRoomBlocked(YoupinError): ...


# ---- 短信登录错误码（YouPin 自己的错误码表，PC 端 sendSmsCode / doLogin 会返回）----
# 84101/84103/84104/85100/5050 之外，这批是从 YouPin 前端能核实到的码表里确认的，一并注册成
# 独立异常类型：裸数字 code 冒出来时不好懂，异常类型名 + 兜底可读描述至少能让人一眼看出是什么。
# 服务端的 msg 字段不一定总有内容，所以每个类在 msg 为空时都有自己的兜底描述，不完全依赖它。


class SliderCaptchaRequired(YoupinError):
    """1110205 SLIDER：登录前必须先完成滑块验证码。这是本应用发现 SMS 登录被卡住的地方——
    验证码本身没法用代码解决或绕过，唯一出路是 `set-token`（人工在浏览器登录后粘贴 token）。"""

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "需要滑块验证码（SLIDER）才能继续登录，短信登录暂时无法使用；"
                                       "请改用 `set-token`：在浏览器登录后从 localStorage 取出 token 粘贴。")


class SmsCodeAbsent(YoupinError):
    """1110201 ABSENT：短信验证码缺失（未发送或未提交）。"""

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "短信验证码缺失（ABSENT）。")


class SmsCodeExpired(YoupinError):
    """1110202 EXPIRED：短信验证码已过期。"""

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "短信验证码已过期（EXPIRED），请重新获取。")


class UpwardSmsRequired(YoupinError):
    """1110206 UPWARDSMS：需要通过上行短信完成验证。"""

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "需要通过上行短信完成验证（UPWARDSMS）。")


class AccountUnderRecovery(YoupinError):
    """1110207 RECOVERY：账号处于找回/恢复流程中。"""

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "账号当前处于找回/恢复流程中（RECOVERY），暂时无法登录。")


class LoginProhibited(YoupinError):
    """1110208 PROHIBITLOGIN：账号被禁止登录。"""

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "账号已被禁止登录（PROHIBITLOGIN）。")


class SteamNotBound(YoupinError):
    """2005 unBoundSteam：账号尚未绑定 Steam。"""

    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(code, msg or "账号尚未绑定 Steam（unBoundSteam）。")


_CODE_MAP = {
    "84101": LoginRequired,
    "84103": LoginRequired,
    "84104": RiskControl,
    "85100": VersionOrUkRejected,
    "5050": MachineRoomBlocked,
    "1110201": SmsCodeAbsent,
    "1110202": SmsCodeExpired,
    "1110205": SliderCaptchaRequired,
    "1110206": UpwardSmsRequired,
    "1110207": AccountUnderRecovery,
    "1110208": LoginProhibited,
    "2005": SteamNotBound,
}


def raise_for_code(code: int | str | None, msg: str = "") -> None:
    if code is None or str(code) == "0":
        return
    raise _CODE_MAP.get(str(code), YoupinError)(str(code), msg)
