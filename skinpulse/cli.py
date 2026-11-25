from __future__ import annotations

import json
import shutil
import time
from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar

import typer

from skinpulse.adapters.youpin.token import normalize_token
from skinpulse.config import EXAMPLE_PATH
from skinpulse.secrets import KEY_YOUPIN_PHONE, KEY_YOUPIN_TOKEN

app = typer.Typer(help="SkinPulse - YouPin buy-order rank monitor", no_args_is_help=True)
dump_app = typer.Typer(help="Dump raw API responses for fixture calibration")
app.add_typer(dump_app, name="dump")
fills_app = typer.Typer(help="求购成交：消失的求购单查去向（成交推送 spec）")
app.add_typer(fills_app, name="fills")
pnl_app = typer.Typer(help="盈亏统计（盈亏 spec）：导入悠悠买卖历史、算持仓快照、看摘要")
app.add_typer(pnl_app, name="pnl")
service_app = typer.Typer(help="Windows 开机自启：install / status / stop / start / restart / uninstall")
app.add_typer(service_app, name="service")

# How many times `set-token`/`probe-token` will call the validating/probing endpoint before
# giving up, and how long to wait between attempts. YouPin returns an empty response body
# under light throttling (observed roughly 1-in-10 rapid requests during this project's own
# API exploration), which `_handle_response` maps to `RateLimited` - a transient hiccup, not
# a rejected token. A handful of quick retries is enough to ride that out without turning a
# real rejection into a long hang.
_VALIDATE_ATTEMPTS = 3
_VALIDATE_RETRY_DELAY_S = 2.0

_T = TypeVar("_T")


def tolerate_console_encoding(*streams: object) -> None:
    """控制台编不进去的字符换成 `?`，而不是抛 UnicodeEncodeError。

    中文 Windows 控制台是 GBK，饰品名里的「™」（StatTrak™）编不进去，`brief` 预览会直接崩。
    只改出错策略、不改编码：强行改成 UTF-8 会让 GBK 控制台整屏乱码。
    """
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _accounts(rt) -> dict:
    """启用的悠悠号（多账号 spec §9）。测试里的替身 rt 没有 accounts 时就是号 1。"""
    from skinpulse.services.account import AccountRuntime
    accs = getattr(rt, "accounts", None)
    return accs if accs else {1: AccountRuntime(1, rt.client)}


def _rt(settings: Path | None, *, console_level: str = "INFO"):
    from skinpulse.config import load_settings
    from skinpulse.logging_setup import setup_logging
    from skinpulse.runtime import build_runtime

    # Logging must be configured *before* anything that can log at startup. The case
    # that forced this: the pending-notification sweep's "recovered N dangling pending
    # notification(s)" warning - the most diagnostic message a stuck-notification
    # recovery can produce - went to an unconfigured root logger (stderr only) and never
    # reached the log file. That sweep has since moved to scheduler.build_scheduler,
    # which `run` calls right after this function returns, so the ordering still has to
    # hold here rather than inside build_runtime. build_runtime's own
    # signature only takes a settings path, not a Settings object, so load settings
    # once here (cheap - just a TOML read) purely to resolve the data directory for
    # setup_logging; build_runtime loads it again itself.
    s = load_settings(settings)
    setup_logging(s.data_path, console_level=console_level)
    return build_runtime(settings)


# 规范化挪到了 adapters/youpin/token.py，网页粘贴 token 也用它（看板三期）。
_normalize_token = normalize_token


def _read_token_from_file(path: Path) -> str:
    """Read+normalize a token from a file (the `--from-file` alternative to the hidden
    prompt). Applies the exact same normalization as the prompt path, then additionally
    rejects anything that still contains whitespace afterwards - a token with an interior
    space or newline is a paste artifact (e.g. a devtools value copied with its wrapped
    display line breaks still in it), not a usable token, and would otherwise surface later
    as a confusing rejection instead of a clear one right here."""
    try:
        # utf-8-sig：Windows 上 PowerShell 5.1 的 Out-File/Set-Content -Encoding utf8、以及一些编辑器，
        # 存出来的文件开头带一个 BOM（U+FEFF）。它不可见、也不算空白，不去掉的话会原样拼进 token，
        # 服务端只会回一句「token 不对」。没有 BOM 的文件读起来和 utf-8 完全一样。
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        typer.echo(f"文件不存在：{path}，未保存。")
        raise typer.Exit(1)
    except (OSError, UnicodeDecodeError) as e:
        typer.echo(f"读取文件失败（{type(e).__name__}）：{path}，未保存。")
        raise typer.Exit(1)

    token = _normalize_token(raw)
    if not token:
        typer.echo(f"文件为空（或去除空白/引号/Bearer 前缀后为空）：{path}，未保存。")
        raise typer.Exit(1)
    if any(ch.isspace() for ch in token):
        typer.echo("文件内容规范化后仍包含内部空白字符（很可能是复制时把换行/空格也带进来了），"
                   "这是常见的粘贴伪影，请检查文件内容后重试；未保存。")
        raise typer.Exit(1)
    return token


def _call_with_rate_limit_retries(fn: Callable[[], _T], *, what: str,
                                   attempts: int = _VALIDATE_ATTEMPTS,
                                   delay_s: float = _VALIDATE_RETRY_DELAY_S) -> _T:
    """Call `fn()` up to `attempts` times, retrying only on `RateLimited` - YouPin's empty-
    body-under-throttling response (see client.py's `_handle_response`), a transient
    condition unrelated to whether the token itself is good. Anything else (`LoginRequired`,
    any other `YoupinError`, or a non-YouPin exception) is not retried and propagates
    immediately - retrying wouldn't change a definite rejection or an unrelated bug.

    Prints a short status line before each retry so a run that takes a few extra seconds
    reads as "retrying", not as a hang. Once attempts are exhausted, re-raises the last
    `RateLimited` so the caller can classify it as "could not verify" rather than "invalid
    token"."""
    from skinpulse.adapters.youpin.errors import RateLimited

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RateLimited as e:
            if attempt >= attempts:
                raise
            typer.echo(f"{what}暂时失败（{e.code}：YouPin 返回了空响应，通常是短时间内请求过于"
                       f"频繁），{delay_s:g} 秒后重试第 {attempt + 1}/{attempts} 次...")
            time.sleep(delay_s)
    raise AssertionError("unreachable")  # attempts >= 1 always taken above


@app.command("init-config")
def init_config() -> None:
    """Copy settings.example.toml to settings.toml."""
    target = Path("settings.toml")
    if target.exists():
        typer.echo("settings.toml already exists")
        raise typer.Exit(0)
    shutil.copy(EXAMPLE_PATH, target)
    typer.echo("created settings.toml")


@app.command()
def login(settings: Path | None = typer.Option(None, "--settings")) -> None:
    """SMS login to YouPin, store token, then sync my purchase orders.

    YouPin's PC login currently demands a slider CAPTCHA (code 1110205) before it will
    send the SMS code, which this command cannot solve or bypass. If you hit that, use
    `set-token` instead: log in at youpin898.com in a real browser, open devtools and run
    `localStorage.getItem('LOGIN_TOKEN')`, then paste the result there.
    """
    from skinpulse.adapters.youpin import auth
    from skinpulse.adapters.youpin.errors import SliderCaptchaRequired
    from skinpulse.services.account import sync_my_orders

    rt = _rt(settings)
    phone = typer.prompt("手机号")
    try:
        sid = auth.send_sms_code(rt.client, phone)
    except SliderCaptchaRequired:
        typer.echo("悠悠有品要求先完成滑块验证码，短信登录暂时走不通。请改用 `set-token`：在浏览器里"
                   "登录 youpin898.com 后，从 devtools 的 localStorage 取出 LOGIN_TOKEN 粘贴进去。")
        raise typer.Exit(1)
    code = typer.prompt("短信验证码", hide_input=True)
    token = auth.sms_sign_in(rt.client, phone, code, sid)
    rt.secrets.set(KEY_YOUPIN_TOKEN, token)
    rt.secrets.set(KEY_YOUPIN_PHONE, phone[:3] + "****" + phone[-4:])
    with rt.session_factory() as s:
        n = sync_my_orders(s, rt.client)
    typer.echo(f"登录成功，已同步 {n} 张求购")


@app.command("set-token")
def set_token(settings: Path | None = typer.Option(None, "--settings"),
              from_file: Path | None = typer.Option(
                  None, "--from-file",
                  help="从文件读取 token 而不是交互式粘贴（token 是约 300+ 字符的长字符串，"
                       "隐藏输入的提示符看不出粘贴是否完整落地）。校验通过后会提醒删除该文件。"),
              account: int = typer.Option(1, "--account", min=1,
                                          help="给第几个悠悠号换 token（多账号；不存在就在校验通过后新建）")) -> None:
    """Paste a YouPin session token copied from a browser, validate it, then sync.

    Workaround for `login`'s slider CAPTCHA (code 1110205): log in at youpin898.com in a
    real browser as a human, open devtools, run `localStorage.getItem('LOGIN_TOKEN')`,
    copy the value, then run this command and paste it when prompted (or save it to a file
    and pass `--from-file` - the hidden prompt can't be visually double-checked for a
    ~300-character paste). The token is validated against the account-info endpoint
    *before* it is saved, so a bad paste is reported immediately instead of surfacing later
    as a confusing failure. A validation failure is classified precisely: `LoginRequired`
    means the token really is invalid/expired; any other error (most commonly `RateLimited`
    from YouPin's occasional empty response under light throttling) means verification
    could not complete this time, which is *not* the same as the token being wrong - the
    token is not saved either way, but the message says which case it is.
    """
    from skinpulse.adapters.youpin.client import YoupinClient
    from skinpulse.adapters.youpin.errors import LoginRequired, YoupinError
    from skinpulse.secrets import youpin_token_key
    from skinpulse.services.account import AccountRuntime, sync_my_orders, validate_token

    rt = _rt(settings)
    # 号 1 照旧用 rt.client；别的号用它自己的 client（设备身份），新号用一个新的设备身份
    live = _accounts(rt).get(account)
    client = rt.client if account == 1 else (live.client if live is not None else YoupinClient(rt.settings.youpin))
    if from_file is not None:
        token = _read_token_from_file(from_file)
    else:
        raw = typer.prompt("Token", hide_input=True)
        token = _normalize_token(raw)
    if not token:
        typer.echo("token 为空，未保存。")
        raise typer.Exit(1)

    def on_retry(n: int, e) -> None:
        typer.echo(f"token 校验暂时失败（{e.code}：YouPin 返回了空响应，通常是短时间内请求过于"
                   f"频繁），{_VALIDATE_RETRY_DELAY_S:g} 秒后重试第 {n}/{_VALIDATE_ATTEMPTS} 次...")

    try:
        # 一次性进程，直接用 rt.client 校验即可（validate_token 会把候选 token 设上去）
        info = validate_token(token, lambda: client, attempts=_VALIDATE_ATTEMPTS,
                              delay_s=_VALIDATE_RETRY_DELAY_S, sleep=time.sleep, on_retry=on_retry)
    except LoginRequired:
        typer.echo("token 无效或已过期，未保存。请在浏览器里重新登录一次，复制一个新的 token 再试。")
        raise typer.Exit(1)
    except YoupinError as e:
        typer.echo(f"暂时无法验证这个 token（{type(e).__name__} {e.code}）——校验请求这次没能跑完，"
                   f"跟这个 token 本身对不对是两码事；token 未保存。过几秒重试一次通常就好。")
        raise typer.Exit(1)

    if account != 1:
        from sqlalchemy import select

        from skinpulse.db.models import Account
        with rt.session_factory() as s:
            dup = s.scalar(select(Account).where(Account.platform == "youpin", Account.user_id == info.user_id,
                                                 Account.id != account))
            if dup is not None:
                typer.echo(f"这个 token 是另一个号（{dup.display_name()}，第 {dup.id} 个）的，未保存。")
                raise typer.Exit(1)
            acc = s.get(Account, account) or Account(id=account, platform="youpin", enabled=True)
            acc.device_id = acc.device_id or client.device_id
            s.add(acc)
            s.commit()
    rt.secrets.set(youpin_token_key(account), token)
    typer.echo(f"token 有效，账号：{info.nickname}（userId={info.user_id}），已保存。")
    if from_file is not None:
        typer.echo(f"提醒：{from_file} 里仍然是一份明文 token，请手动删除该文件（不会自动删除）。")
    with rt.session_factory() as s:
        n = sync_my_orders(s, rt.client if account == 1 else AccountRuntime(account, client))
    typer.echo(f"登录成功，已同步 {n} 张求购")


@app.command("probe-token")
def probe_token(settings: Path | None = typer.Option(None, "--settings"),
                 from_file: Path | None = typer.Option(
                     None, "--from-file",
                     help="从文件读取 token 而不是交互式粘贴，规范化规则与 set-token 相同。")) -> None:
    """Diagnose a pasted token against a few read-only endpoints, without ever saving it.

    Use this when `set-token` fails with something other than a clear "token invalid/expired"
    message - e.g. a `badjson` error - and you cannot tell whether the token itself was
    rejected or the server simply answered in a shape this app does not parse (see the
    README troubleshooting section). Prompts for a token exactly like `set-token` does
    (input hidden, same normalization, or read it from a file via `--from-file`), then
    probes three read-only account endpoints with it, plus - once, as a baseline - the same
    three with *no* token at all. Each probe retries a few times on `RateLimited` (YouPin's
    empty-response-under-throttling case) before it is reported, so a transient empty
    response reads as a retried observation rather than an immediate probe failure. For
    each probe it prints only the HTTP status, content-type, whether an `sk` response
    header was present, the body length, and either the parsed code/msg (JSON body) or a
    short truncated excerpt (non-JSON body). It never prints the token, never touches a
    request header or `uk` in its output, and never calls into the secret store: nothing is
    saved, whatever the outcome.
    """
    from skinpulse.adapters.youpin.client import (
        PATH_SEARCH_MY_PURCHASE_ORDERS, PATH_USER_INFO, PATH_USER_INFO_LEGACY,
    )

    rt = _rt(settings)
    if from_file is not None:
        token = _read_token_from_file(from_file)
    else:
        raw = typer.prompt("Token", hide_input=True)
        token = _normalize_token(raw)
    if not token:
        typer.echo("token 为空，未探测。")
        raise typer.Exit(1)

    client = rt.client
    endpoints = [
        # 已经用真实 token 确认过：这个接口在 sk 加密通道后面（344 字符的 sk 请求头、十六进制
        # 响应体），下面"非 JSON / 形状异常"是它的正常状态，不代表这个 token 有问题——判断 token
        # 是否有效请看另外两个接口。留着探测它只是为了让这份对比诊断保持完整。
        ("getUserInfoForApp（sk 加密通道，预期非 JSON，非探测失败）", "GET", client.s.pc_api_base,
         PATH_USER_INFO, None, client._pc_headers),
        ("getUserInfo(旧接口/App host)", "GET", client.s.app_api_base, PATH_USER_INFO_LEGACY, None,
         client._app_headers),
        ("searchPurchaseOrderList", "POST", client.s.pc_api_base, PATH_SEARCH_MY_PURCHASE_ORDERS,
         {"pageIndex": 1, "pageSize": 1, "status": 20}, client._pc_headers),
    ]

    def probe_one(name, method, base, path, body, headers_factory, use_token):
        try:
            r = _call_with_rate_limit_retries(
                lambda: client._probe(method, path, base=base, headers_factory=headers_factory,
                                      json=body, token=use_token),
                what=f"{name} 探测",
            )
        except Exception as e:  # 单个接口探测失败不该拖垮其它接口的诊断
            return None, f"{name}: 探测失败（{type(e).__name__}）"
        if r.is_json:
            line = (f"{name}: HTTP {r.status_code}  content-type={r.content_type or '<none>'}  "
                   f"sk头={'有' if r.has_sk_header else '无'}  body长度={r.body_length}  "
                   f"code={r.code}  msg={r.msg!r}")
        else:
            line = (f"{name}: HTTP {r.status_code}  content-type={r.content_type or '<none>'}  "
                   f"sk头={'有' if r.has_sk_header else '无'}  body长度={r.body_length}  "
                   f"非 JSON，摘要（截断）={r.excerpt!r}")
        return r, line

    typer.echo("=== 基线：不带 token（预期都应判定为未登录）===")
    baseline_all_rejected = True
    for name, method, base, path, body, headers_factory in endpoints:
        r, line = probe_one(name, method, base, path, body, headers_factory, None)
        typer.echo(line)
        if r is None or not (r.is_json and str(r.code) in ("84101", "84103")):
            baseline_all_rejected = False
    typer.echo("基线小结：" + ("三个接口不带 token 时全部按预期返回 84101/84103（未登录）。"
                            if baseline_all_rejected else
                            "并非全部按预期返回 84101/84103——基线本身不寻常，下面 token 的探测"
                            "结果仅供参考，不能直接当作 token 是否有效的结论。"))

    typer.echo("=== 用你刚粘贴的 token 探测 ===")
    accepted, rejected, unclear = [], [], []
    for name, method, base, path, body, headers_factory in endpoints:
        r, line = probe_one(name, method, base, path, body, headers_factory, token)
        typer.echo(line)
        if r is None or not r.is_json:
            unclear.append(name)
        elif str(r.code) in ("84101", "84103"):
            rejected.append(name)
        else:
            accepted.append(name)

    typer.echo(
        f"结论：{len(accepted)}/{len(endpoints)} 个接口看起来接受了这个 token"
        f"（{'、'.join(accepted) or '无'}）；{len(rejected)} 个明确按未登录拒绝"
        f"（{'、'.join(rejected) or '无'}）；{len(unclear)} 个响应形状异常、无法判断"
        f"（{'、'.join(unclear) or '无'}）。"
    )


@app.command()
def sync(settings: Path | None = typer.Option(None, "--settings")) -> None:
    from skinpulse.services.account import sync_my_orders
    from skinpulse.services.buff import sync_buff_orders

    rt = _rt(settings)
    with rt.session_factory() as s:
        accs = _accounts(rt)
        n = sum(sync_my_orders(s, a if len(accs) > 1 else rt.client) for a in accs.values())
        typer.echo(f"synced {n} orders")
        # BUFF 号（BUFF spec §8）：只同步求购单，名次等服务里的下一轮数
        for b in sorted(rt.buff_accounts.values(), key=lambda a: a.id):
            typer.echo(f"BUFF {b.label}: synced {len(sync_buff_orders(s, b))} orders")


@app.command("set-buff-cookie")
def set_buff_cookie(settings: Path | None = typer.Option(None, "--settings"),
                    account: int | None = typer.Option(None, "--account", min=1,
                                                       help="给哪个 BUFF 号换 session（不给就新加一个 BUFF 号）")) -> None:
    """粘贴浏览器里 BUFF 的 session cookie（开发者工具 → Application → Cookies → buff.163.com → session），
    校验通过才保存（BUFF spec §2）。输入不回显，cookie 不进日志。服务要重启或在看板上操作才会用新的号。"""
    import uuid

    from skinpulse.adapters.buff.client import BuffClient
    from skinpulse.adapters.buff.errors import BuffError, BuffLoginRequired
    from skinpulse.db.models import Account
    from skinpulse.db.session import utcnow
    from skinpulse.secrets import buff_cookie_key
    from skinpulse.web.buff_account import normalize_cookie

    rt = _rt(settings)
    cookie = normalize_cookie(typer.prompt("session", hide_input=True))
    if not cookie:
        typer.echo("没读到 session（中间不能有空格或换行）。", err=True)
        raise typer.Exit(2)
    with rt.session_factory() as s:
        row = s.get(Account, account) if account else None
        if account and (row is None or row.platform != "buff"):
            typer.echo(f"没有 BUFF 号 {account}。不给 --account 就新加一个。", err=True)
            raise typer.Exit(2)
        device_id = (row.device_id if row else None) or uuid.uuid4().hex.upper()
    client = BuffClient(rt.settings.buff, cookie, device_id)
    try:
        me = client.whoami()
    except BuffLoginRequired:
        typer.echo("session 无效或已过期，没有保存。", err=True)
        raise typer.Exit(1) from None
    except BuffError as e:
        typer.echo(f"暂时无法验证（{e.code}），没有保存。", err=True)
        raise typer.Exit(1) from None
    with rt.session_factory() as s:
        row = s.get(Account, account) if account else None
        if row is None:
            row = Account(platform="buff", enabled=True, created_at=utcnow())
            s.add(row)
        elif row.platform_uid and me.uid and row.platform_uid != me.uid:
            typer.echo("这是另一个 BUFF 号的 session，没有保存。", err=True)
            raise typer.Exit(1)
        row.platform_uid = row.platform_uid or me.uid
        row.nickname = me.nickname or row.nickname
        row.device_id, row.token_valid = device_id, True
        s.commit()
        aid = row.id
    rt.secrets.set(buff_cookie_key(aid), cookie)
    typer.echo(f"session 有效，已保存到 BUFF 号 {aid}（{me.nickname or '昵称读不到'}）。")
    typer.echo("监控服务重启后开始按 15 分钟一轮查 BUFF（看板「系统」页添加的不用重启）。")


_TIER_TITLE = {"inverted": "倒挂（我的价 ≥ 在售最低）", "top1": "直接第 1", "near": "离第 1 差 5% 内",
               "near_sell": "贴近在售（≥ 90%）"}


def _paused_line(it: dict) -> str:
    from decimal import Decimal

    from skinpulse.core.messages import sell_label

    name = ("★ " if it["star"] else "") + it["title"] + (f"（{it['tag']}）" if it["tag"] else "")
    if it["tier"] in ("inverted", "top1") and it["top1_kind"] == "alone":
        after = "第 1 · 没人求购"
    elif it["top1_kind"] == "tie":
        after = "并列第 1"
    elif it["over"] is not None:
        after = f"第 1 · 高出 ¥{it['over']:g}"
    elif it["gap_ratio"] is not None:
        after = f"差 {it['gap_ratio'] * 100:.1f}%"
    else:
        after = "—"
    sell = sell_label(None if it["sell_min"] is None else Decimal(str(it["sell_min"])), it["sell_depth"])
    src = {"youpin": "实时", "steamdt": "SteamDT"}.get(it["sell_src"] or "", "")
    ratio = "—" if it["sell_ratio"] is None else f"{it['sell_ratio'] * 100:.0f}%"
    top = "—" if it["top_bid"] is None else f"{it['top_bid']:g}"
    days = "" if it["paused_days"] is None else f" · 暂停 {it['paused_days']} 天"
    return (f"  {name}{days}\n    我的价 {it['price']:g} × {it['remaining_qty']} · 恢复后 {after} · 第 1 名 {top}"
            f" · 在售 {sell}{f'（{src}）' if src else ''} · 我的价/在售 {ratio}")


@app.command("check-paused")
def check_paused(account: int | None = typer.Option(None, "--account", help="只查这个号（默认全部启用的号）"),
                 as_json: bool = typer.Option(False, "--json", help="输出和 /api/paused 一样的 JSON"),
                 no_steamdt: bool = typer.Option(False, "--no-steamdt", help="不查 SteamDT，只用悠悠在售接口核对"),
                 settings: Path | None = typer.Option(None, "--settings")) -> None:
    """充值前检查暂停单（暂停单 spec §6）：恢复后会不会倒挂、直接第 1。只读、不推送；约 8 分钟。

    服务在跑时优先用看板总览的「检查一次」：那个和每轮监控错开，这里不错开。"""
    import json

    from skinpulse.db.session import utcnow
    from skinpulse.services.paused import PausedRefused, run_check, start_run
    from skinpulse.web.paused import paused_payload

    rt = _rt(settings)
    if account is not None and account not in _accounts(rt):
        typer.echo(f"没有启用的号 {account}", err=True)
        raise typer.Exit(1)
    with rt.session_factory() as s:
        try:
            run_id = start_run(s, rt.monitor, "cli", utcnow())
        except PausedRefused as e:
            typer.echo(e.message, err=True)
            raise typer.Exit(1) from None
    res = run_check(rt.monitor, run_id, account_ids=None if account is None else [account],
                    use_steamdt=not no_steamdt, progress=lambda m: typer.echo(m, err=True))
    if res.status != "done":
        typer.echo(f"检查失败：{res.error}", err=True)
        raise typer.Exit(1)
    payload = paused_payload(rt, utcnow())
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=1))
        return
    sm = payload["summary"]
    typer.echo(f"暂停中 {sm['total']} 张，占用 ¥{sm['amount']:.2f}；{sm['old']} 张暂停超过 {payload['old_days']} 天")
    if res.skipped:
        typer.echo(f"没查成的号：{'、'.join(str(a) for a in res.skipped)}（登录失效或被限流）")
    for tier, title in _TIER_TITLE.items():
        rows = [it for it in payload["items"] if it["tier"] == tier]
        t = sm["tiers"][tier]
        typer.echo(f"\n{title}：{t['count']} 张，占用 ¥{t['amount']:.2f}")
        for it in rows:
            typer.echo(_paused_line(it))
    typer.echo(f"\n其余 {sm['tiers']['other']['count']} 张恢复后不靠前。")


@app.command("steamdt-key")
def steamdt_key(settings: Path | None = typer.Option(None, "--settings"),
                from_file: Path | None = typer.Option(None, "--from-file")) -> None:
    """保存 SteamDT 开放平台的 API key（和悠悠 token 一样进系统钥匙串，不进仓库）。"""
    from skinpulse.adapters.steamdt.client import SteamDTClient
    from skinpulse.adapters.steamdt.errors import SteamDTAuthError, SteamDTError
    from skinpulse.secrets import KEY_STEAMDT

    rt = _rt(settings)
    raw = _read_token_from_file(from_file) if from_file else typer.prompt("SteamDT API key", hide_input=True)
    key = raw.strip()
    if not key:
        typer.echo("key 为空，未保存。")
        raise typer.Exit(1)
    # 先验证再保存，和 set-token 一个道理：粘错了当场就说，而不是等某次对账才发现。
    client = SteamDTClient(key, base_url=rt.settings.steamdt.base_url)
    try:
        client.get_prices("AK-47 | Redline (Field-Tested)")
    except SteamDTAuthError:
        # 2007 的服务端文案是「请先申请ApiKey」，会把人引去重新申请；这里说人话。
        typer.echo("这个 key 服务端不认（多半是拼错了，或者已经被「重新生成」作废）。未保存。")
        raise typer.Exit(1)
    except SteamDTError as e:
        typer.echo(f"暂时无法验证（{e}），未保存。稍后再试。")
        raise typer.Exit(1)
    rt.secrets.set(KEY_STEAMDT, key)
    typer.echo("key 有效，已保存。")
    if from_file:
        typer.echo(f"提醒：{from_file} 里还留着一份明文 key，请手动删除。")


@app.command("steamdt-items")
def steamdt_items(settings: Path | None = typer.Option(None, "--settings"),
                  from_file: Path | None = typer.Option(
                      None, "--from-file", help="从落盘的 /base 响应读取，不消耗当天额度")) -> None:
    """同步 SteamDT 饰品目录，建立 悠悠 templateId ↔ marketHashName 映射。

    **`/base` 每天只能调一次**（约 11 MB / 4.4 万条）。调过之后当天就没有第二次了，所以
    正常做法是把响应存成文件、之后都用 --from-file 重建。
    """
    import json as _json

    from skinpulse.adapters.steamdt.models import BaseItem
    from skinpulse.services.market import sync_base_items

    rt = _rt(settings)
    if from_file:
        body = _json.loads(from_file.read_text(encoding="utf-8"))
        items = [BaseItem.from_api(x) for x in (body.get("data") or [])]
        typer.echo(f"从 {from_file} 读到 {len(items)} 条")
    else:
        typer.confirm("这会消耗掉 /base 今天唯一的一次调用额度，继续？", abort=True)
        items = _steamdt_client(rt).get_base_items()
        typer.echo(f"从接口拉到 {len(items)} 条")
    with rt.session_factory() as s:
        stored, skipped = sync_base_items(s, items)
    typer.echo(f"写入 {stored} 条；跳过 {skipped} 条（没有悠悠 itemId，对不上账）")


@app.command("market-check")
def market_check(settings: Path | None = typer.Option(None, "--settings"),
                 limit: int | None = typer.Option(None, "--limit", help="只对账前 N 个模板")) -> None:
    """多平台对账：悠悠的价格相对同类平台是偏高还是偏低，偏多少。"""
    from skinpulse.services.market import compare_platforms, format_report

    rt = _rt(settings)
    with rt.session_factory() as s:
        rep = compare_platforms(s, _steamdt_client(rt), batch_size=rt.settings.steamdt.batch_size,
                                limit=limit, progress=typer.echo)
    typer.echo("")
    typer.echo(format_report(rep))


def _steamdt_client(rt):
    from skinpulse.adapters.steamdt.client import SteamDTClient
    from skinpulse.secrets import KEY_STEAMDT

    key = rt.secrets.get(KEY_STEAMDT)
    if not key:
        typer.echo("还没配置 SteamDT API key，先跑一次 `steamdt-key`。")
        raise typer.Exit(1)
    return SteamDTClient(key, base_url=rt.settings.steamdt.base_url)


@app.command("price-snapshot")
def price_snapshot_cmd(settings: Path | None = typer.Option(None, "--settings")) -> None:
    """给活跃求购单涉及的饰品存一份今天的多平台价格（SteamDT，约 5 分钟；同一天重跑只补缺的）。"""
    # 故意不提供日期参数：price/batch 只给当前价，记到别的日期上就是假历史。
    from skinpulse.core.localday import local_today
    from skinpulse.db.session import utcnow
    from skinpulse.services.price_snapshot import take_price_snapshot

    rt = _rt(settings)
    d = local_today(rt.settings.timezone)
    with rt.session_factory() as s:
        rep = take_price_snapshot(s, _steamdt_client(rt), day=d, batch_size=rt.settings.steamdt.batch_size,
                                  now=utcnow(), sleep=time.sleep)
    typer.echo(str(rep))


@app.command("valuation")
def valuation_cmd(settings: Path | None = typer.Option(None, "--settings"),
                  day: str | None = typer.Option(None, "--date", help="估值哪一天（本地日期 YYYY-MM-DD），默认昨天"),
                  dry_run: bool = typer.Option(False, "--dry-run", help="照常取数和计算，但不写库（只打印汇总）")) -> None:
    """给活跃求购单的饰品估值：存悠悠日线、算价位和扣费差价（价位 spec §5）。

    会真的请求 SteamDT：每个饰品一次 K 线，每分钟最多 [valuation] rate_per_min 次；每次请求本身约 1.7 秒，约 430 个要 12 分钟左右。
    不向悠悠发任何请求。同一天重跑是安全的。"""
    from datetime import timedelta

    from skinpulse.core.localday import local_today, parse_day
    from skinpulse.db.session import utcnow
    from skinpulse.services import valuation as val

    rt = _rt(settings)
    try:
        d = parse_day(day) if day else local_today(rt.settings.timezone) - timedelta(days=1)
    except ValueError:
        raise typer.BadParameter("日期格式是 YYYY-MM-DD", param_hint="--date")
    client = _steamdt_client(rt)
    with rt.session_factory() as s:
        rep = val.run_valuation(s, client, getattr(rt, "market", None), rt.settings, day=d, now=utcnow(),
                                sleep=time.sleep, dry_run=dry_run)
    typer.echo(str(rep))
    if dry_run:
        typer.echo("（dry-run：没有写库。）")


@app.command("backtest")
def backtest_cmd(settings: Path | None = typer.Option(None, "--settings"),
                 out: Path | None = typer.Option(None, "--out", help="把 Markdown 报告写进这个文件（默认打印）"),
                 split: str | None = typer.Option(None, "--split", help="H2 从哪天起（YYYY-MM-DD），默认 2026-04-01；每季度重跑时往后挪")) -> None:
    """价位二期的 walk-forward 回测（价位 spec §9）：检验「7 天坏情况」准不准、「在跌且跑输大盘」能不能当撤单理由。

    只读库里的自有日线、大盘和成交记录，不请求任何外部接口，随时可以重跑。"""
    from skinpulse.core.localday import parse_day
    from skinpulse.services import backtest as bts

    try:
        h2 = parse_day(split) if split else bts.bt.H2_START
    except ValueError:
        raise typer.BadParameter("日期格式是 YYYY-MM-DD", param_hint="--split")
    rt = _rt(settings, console_level="WARNING")
    with rt.session_factory() as s:
        text = bts.render(bts.run_backtest(s, rt.settings, h2))
    if out is None:
        typer.echo(text)
    else:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"报告已写到 {out}")


_OUTCOME_LABEL = {"done": "成交", "likely_done": "推测成交（交易中过，已完成里没有）", "paused": "暂停（余额不足）", "missing": "不见了（多半是撤单）",
                  None: "没查到（翻到页数上限）"}


def _money(v) -> str:
    if v is None:
        return "—"
    return ("−" if v < 0 else "") + f"¥{abs(v):,.2f}"


def _signed(v) -> str:
    if v is None:
        return "—"
    return ("−" if v < 0 else "+") + f"¥{abs(v):,.2f}"


def _pnl_summary(rt, account: int | None, basis: str) -> None:
    from skinpulse.web.pnl import pnl_payload

    p = pnl_payload(rt, account)
    if p["as_of"] is None:
        typer.echo("还没有盈亏数据：先跑 `pnl import`。")
        return
    h, r, w = p["holding"], p["realized"], p["where"]
    value = h["value_net"] if basis == "net" else h["value_mkt"]
    flt = value - h["cost_priced"]
    typer.echo(f"{p['as_of']} · 浮动按{'到手价' if basis == 'net' else '市价'}")
    typer.echo(f"总盈亏 {_signed(r['total'] + flt)}")
    typer.echo(f"  已实现 {_signed(r['total'])}（{r['matched']} 笔配上了买入；另 {r['unknown']} 笔成本未知，"
               f"到手 {_money(r['unknown_net'])}，不算）")
    typer.echo(f"  浮动 {_signed(flt)} · 持有 {h['qty']} 件 · 成本 {_money(h['cost'])} · "
               f"{'到手' if basis == 'net' else '市值'} {_money(value)}")
    typer.echo(f"  在哪：库存 {w['inv']} · 租出 {w['lease']} · 不在库存 {w['unknown']}（成本 {_money(w['unknown_cost'])}，多半在 BUFF App 里出租）")
    if h["unpriced_qty"]:
        typer.echo(f"  {h['unpriced_qty']} 件没有市价，不在市值里")
    if p["youpin"]:
        typer.echo(f"  悠悠自己算的浮动（库存里有买入价的 {p['youpin']['n']} 件）：{_signed(p['youpin']['pnl'])}")


@pnl_app.command("import")
def pnl_import(settings: Path | None = typer.Option(None, "--settings")) -> None:
    """一次性导入每个号的全部买卖历史、补全部卖出到手价，再算今天的持仓快照（盈亏 spec §5.3）。

    会真的请求悠悠（只读）：买入、卖出列表翻到底（每页 20 笔），每张卖出 1 个详情，库存 1 个，租出几页，
    3 秒左右一个。可以重复跑：已入库的订单不会重复。"""
    from skinpulse.core.localday import local_today
    from skinpulse.db.session import utcnow
    from skinpulse.services.pnl import run_pnl

    rt = _rt(settings)
    now = utcnow()
    rep = run_pnl(rt, local_today(rt.settings.timezone, now), now, import_all=True, echo=typer.echo)
    typer.echo(str(rep))
    _pnl_summary(rt, None, rt.settings.pnl.basis)


@pnl_app.command("run")
def pnl_run(settings: Path | None = typer.Option(None, "--settings"),
            day: str | None = typer.Option(None, "--day", help="算哪天（YYYY-MM-DD），默认今天")) -> None:
    """手动跑一次每日任务（增量同步 + 快照）。会真的请求悠悠（只读），平时 10 个请求以内。"""
    from datetime import date as _date

    from skinpulse.core.localday import local_today
    from skinpulse.db.session import utcnow
    from skinpulse.services.pnl import run_pnl

    rt = _rt(settings)
    now = utcnow()
    d = _date.fromisoformat(day) if day else local_today(rt.settings.timezone, now)
    typer.echo(str(run_pnl(rt, d, now, echo=typer.echo)))


@pnl_app.command("show")
def pnl_show(settings: Path | None = typer.Option(None, "--settings"),
             basis: str | None = typer.Option(None, "--basis", help="market（市价）或 net（到手价）"),
             account: int | None = typer.Option(None, "--account", help="只看某个号")) -> None:
    """打印最近一次盈亏快照的摘要（只读库，不请求任何外部服务）。"""
    rt = _rt(settings)
    b = basis or rt.settings.pnl.basis
    if b not in ("market", "net"):
        raise typer.BadParameter("market 或 net", param_hint="--basis")
    _pnl_summary(rt, account, b)


@fills_app.command("backfill")
def fills_backfill(settings: Path | None = typer.Option(None, "--settings"),
                   max_pages: int = typer.Option(30, "--max-pages", min=1, max=100,
                                                 help="每个列表最多翻几页（每页 40 条）")) -> None:
    """给所有还没查清去向的「消失」事件查一次去向：成交 / 暂停 / 撤单（成交推送 spec §4.5）。

    会真的请求悠悠（只读：「我的求购」换 status 查「已完成」「暂停中」，2 个列表最多各 --max-pages 页）。
    只写去向，**绝不推送**——包括 24 小时内刚消失、监控本来会推的成交：上线时先跑它，再重启服务，
    今天以前已经知道的成交就不会补推一遍。可以重复跑。"""
    from zoneinfo import ZoneInfo

    from sqlalchemy import select

    from skinpulse.db.models import MyBuyOrder, OrderEvent
    from skinpulse.db.session import utcnow
    from skinpulse.services.fills import resolve_gone

    rt = _rt(settings)
    tz = ZoneInfo(rt.settings.timezone)
    with rt.session_factory() as s:
        ids = list(s.scalars(select(OrderEvent.id).where(OrderEvent.kind == "gone", OrderEvent.outcome.is_(None))))
        if not ids:
            typer.echo("没有待查去向的消失事件。")
            return
        # 每个号翻自己的「已完成」「暂停中」（多账号 spec §5）；一个号时和以前一样
        accs = _accounts(rt)
        reps = [resolve_gone(s, a.client, rt.settings, now=utcnow(), max_pages=max_pages, backfill=True,
                             account_id=aid if len(accs) > 1 else None) for aid, a in accs.items()]
        evs = list(s.scalars(select(OrderEvent).where(OrderEvent.id.in_(ids)).order_by(OrderEvent.at)))
        names = dict(s.execute(select(MyBuyOrder.purchase_no, MyBuyOrder.commodity_name)
                               .where(MyBuyOrder.purchase_no.in_({e.purchase_no for e in evs}))).all())
        for e in evs:
            when = f"  {e.outcome_at.astimezone(tz):%m-%d %H:%M}" if e.outcome_at else ""
            typer.echo(f"{e.at.astimezone(tz):%m-%d %H:%M} 消失  {names.get(e.purchase_no, e.purchase_no)}"
                       f"  →  {_OUTCOME_LABEL.get(e.outcome, e.outcome)}{when}")
    for rep in reps:
        typer.echo(str(rep))


@app.command("brief")
def brief_cmd(settings: Path | None = typer.Option(None, "--settings"),
              day: str | None = typer.Option(None, "--date", help="要看哪一天（本地日期 YYYY-MM-DD），默认昨天"),
              send: bool = typer.Option(False, "--send", help="真的推送并记入 brief_log（默认只打印）"),
              force: bool = typer.Option(
                  False, "--force",
                  help="和 --send 一起用：这一天已有记录也重新判定、重发（会用新记录替换掉那一天原来的记录）")) -> None:
    """预览（默认）或发送某一天的每日简报。"""
    from datetime import timedelta

    from skinpulse.core.localday import local_today, parse_day
    from skinpulse.db.session import utcnow
    from skinpulse.analysis.brief import QUIET_SENT, SENT
    from skinpulse.services import brief as brief_svc

    rt = _rt(settings)
    try:
        d = parse_day(day) if day else local_today(rt.settings.timezone) - timedelta(days=1)
    except ValueError:
        raise typer.BadParameter("日期格式是 YYYY-MM-DD", param_hint="--date")
    if force and not send:
        typer.echo("注意：--force 只在配合 --send 时才有效，本次是预览，不受影响。")
    with rt.session_factory() as s:
        if not send:
            plan = brief_svc.plan_brief(s, rt.settings, d)
            typer.echo(f"{d}：{plan.decision.decision}（{plan.decision.reasons.get('summary', '')}）")
            if plan.push is not None:
                typer.echo(f"\n标题：{plan.push.title}\n\n{plan.full_body}")
            typer.echo("\n（预览，没有推送。加 --send 才会真的发。）")
            return
        now = utcnow()
        row = brief_svc.run_daily_brief(s, rt.ntfy, rt.settings, d, now=now, force=force)
    # `run_daily_brief` 在没有 force 的情况下，遇到已有记录会原样把它返回、什么都不做（见其
    # docstring）——那不是这一次真的发生的事，`created_at` 早于这次传进去的 `now` 就说明是这种
    # 情况，不能照常打印 decision/送达提示，否则看起来像是这次真的重新判定、重新推送了一遍。
    if not force and row.created_at < now:
        typer.echo(f"{d}：已有记录（{row.decision}），未重发；加 --force 重发")
        return
    typer.echo(f"{d}：{row.decision}")
    if row.decision in (SENT, QUIET_SENT):
        typer.echo("注意：只代表推送服务收下了，有没有到微信请以手机上实际收到为准。")


@app.command("round")
def round_cmd(settings: Path | None = typer.Option(None, "--settings")) -> None:
    rt = _rt(settings)
    typer.echo(str(rt.monitor.run_round()))


@app.command("notify-test")
@app.command("ntfy-test", hidden=True)   # 旧名字：推送早就不只 ntfy 一条通道了，保留它只为兼容
def ntfy_test(settings: Path | None = typer.Option(None, "--settings")) -> None:
    """往所有配置了的推送通道（PushPlus、ntfy）各发一条测试消息，逐条报告结果。"""
    from skinpulse.core.messages import build_system_message
    from skinpulse.services.notifier import AllChannelsFailed

    rt = _rt(settings)
    if rt.ntfy is None:
        typer.echo("没有配置任何推送通道：先跑 `pushplus-token`（或者设置 ntfy topic）。")
        raise typer.Exit(1)
    try:
        results = rt.ntfy.publish(build_system_message("SkinPulse 测试", "通知通道正常", rt.settings.ntfy))
    except AllChannelsFailed as e:
        results = e.results
    for r in results if isinstance(results, list) else []:
        typer.echo(f"  {r.name}: {'服务端已收下' if r.ok else '失败'}"
                   + ("" if r.ok else f"（{type(r.error).__name__}）"))
    # 「收下」不等于「送到手机」：ntfy 在这台手机上就是返回成功、却从没实时送达过。
    typer.echo("注意：以上只代表各服务端收下了，有没有到手机请以手机上实际收到为准。")
    if isinstance(results, list) and not any(r.ok for r in results):
        raise typer.Exit(1)


@app.command("pushplus-token")
def pushplus_token(settings: Path | None = typer.Option(None, "--settings"),
                   from_file: Path | None = typer.Option(None, "--from-file")) -> None:
    """保存 PushPlus token，并往你的微信发一条测试消息。

    token 进系统钥匙串（和悠悠 token、SteamDT key 一样），不进 settings.toml，更不进仓库。
    """
    from skinpulse.secrets import KEY_PUSHPLUS
    from skinpulse.services import pushplus_setup as pp

    rt = _rt(settings)
    raw = _read_token_from_file(from_file) if from_file else typer.prompt("PushPlus token（输入不回显）",
                                                                           hide_input=True)
    token, bad = pp.check_token_text(raw)
    if bad == pp.EMPTY:
        typer.echo("token 为空或中间带空白（多半是复制时带进了换行），未保存。")
        raise typer.Exit(1)
    if bad == pp.CONTROL:
        typer.echo("输入里混进了看不见的控制字符——在不回显的输入框里按 Ctrl+V 时常见。"
                   "请改用 --from-file（把 token 存进一个文本文件再读），未保存。")
        raise typer.Exit(1)

    res = pp.send_probe(token, rt.settings.timezone)
    if res.reason == pp.AUTH:
        typer.echo("PushPlus 不认这个 token（903）。未保存。")
        typer.echo(f"程序实际收到的 token：{pp.token_shape(token)}。")
        typer.echo("请在网页上点「复制token」，粘进记事本看看位数对不对得上；对不上多半是粘贴没粘完整，"
                   "改用 --from-file 最稳。")
        raise typer.Exit(1)
    if res.reason == pp.REALNAME:
        typer.echo("PushPlus 要求账号先完成实名认证才能发消息（905）。请在官网完成认证后重试。未保存。")
        raise typer.Exit(1)
    if res.reason == pp.RESTRICTED:
        typer.echo("PushPlus 账号当前被限制使用（900）。官方建议停止请求，约 2 天后自动恢复。未保存。")
        raise typer.Exit(1)
    if res.reason == pp.REJECTED:
        # 没映射过的错误码原样打出来：这个服务的错误码表是照文档抄的，还没有全部实测过。
        typer.echo(f"PushPlus 返回错误：{res.detail}。未保存。")
        raise typer.Exit(1)
    if res.reason == pp.NETWORK:
        typer.echo(f"暂时连不上 PushPlus（{res.detail}），未保存。稍后再试。")
        raise typer.Exit(1)
    short_code = res.short_code

    rt.secrets.set(KEY_PUSHPLUS, token)
    typer.echo(f"token 有效，已保存。测试消息已发出（流水号 {short_code}）。")
    typer.echo("请去微信里确认收到——PushPlus 返回成功只代表它收下了，有没有送到微信得你亲眼看。")
    typer.echo("监控服务要重启一次才会开始用这条通道（看板「系统」页录入的不用重启）。")
    if from_file:
        typer.echo(f"提醒：{from_file} 里还留着一份明文 token，请手动删除。")


@dump_app.command("my-orders")
def dump_my_orders(settings: Path | None = typer.Option(None, "--settings")) -> None:
    rt = _rt(settings)
    orders = rt.client.search_my_purchase_orders()
    typer.echo(json.dumps([o.raw for o in orders], ensure_ascii=False, indent=1))


@dump_app.command("buff-orders")
def dump_buff_orders(settings: Path | None = typer.Option(None, "--settings")) -> None:
    """BUFF 号「正在求购」的单（解析后的，不含任何身份信息）。"""
    from dataclasses import asdict

    rt = _rt(settings)
    for b in sorted(rt.buff_accounts.values(), key=lambda a: a.id):
        orders, _ = b.client.my_buy_orders()
        typer.echo(json.dumps({"account": b.id, "orders": [asdict(o) for o in orders]}, ensure_ascii=False,
                              indent=1, default=str))


@dump_app.command("template")
def dump_template(template_id: int, settings: Path | None = typer.Option(None, "--settings")) -> None:
    from skinpulse.adapters.youpin.client import PATH_TEMPLATE_PURCHASE_ORDERS_PC

    rt = _rt(settings)
    data = rt.client._post(PATH_TEMPLATE_PURCHASE_ORDERS_PC,
                           {"templateId": str(template_id), "pageIndex": 1, "pageSize": rt.settings.polling.visible_limit})
    typer.echo(json.dumps(data, ensure_ascii=False, indent=1))


class TradeSide(str, Enum):
    buy = "buy"
    sell = "sell"


class ApiHost(str, Enum):
    pc = "pc"
    app = "app"


# 盈亏统计调研用（docs/research/2026-11-24-pnl-data-sources.md）：打印原始响应。都是查询接口，只读；
# 路径常量在 adapters/youpin/client.py（盈亏同步用同一套）。
from skinpulse.adapters.youpin import client as yc  # noqa: E402


def _dump_host(rt, host: ApiHost) -> str:
    return rt.settings.youpin.pc_api_base if host is ApiHost.pc else rt.settings.youpin.app_api_base


@dump_app.command("trades")
def dump_trades(side: TradeSide, status: int = typer.Option(340, "--status", help="订单状态，340 = 已完成"),
                page: int = typer.Option(1, "--page"), page_size: int = typer.Option(20, "--page-size"),
                host: ApiHost = typer.Option(ApiHost.pc, "--host"),
                settings: Path | None = typer.Option(None, "--settings")) -> None:
    """买入 / 卖出订单列表的一页（只读）。输出含交易对手信息，存 fixture 前要脱敏。"""
    rt = _rt(settings)
    data = rt.client._post(yc.PATH_TRADE_LIST.format(side=side.value),
                           {"keys": "", "orderStatus": status, "pageIndex": page, "pageSize": page_size,
                            "presenterId": 0, "sceneType": 0},
                           base=_dump_host(rt, host), authed=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=1))


@dump_app.command("order")
def dump_order(order_no: str, host: ApiHost = typer.Option(ApiHost.pc, "--host"),
               settings: Path | None = typer.Option(None, "--settings")) -> None:
    """一张买卖订单的详情（只读）。输出含交易对手信息，存 fixture 前要脱敏。"""
    rt = _rt(settings)
    user_id = rt.client.get_user_info().user_id
    data = rt.client._post(yc.PATH_ORDER_DETAIL, {"orderNo": order_no, "userId": user_id},
                           base=_dump_host(rt, host), authed=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=1))


@dump_app.command("lease-out")
def dump_lease_out(page: int = typer.Option(1, "--page"), page_size: int = typer.Option(20, "--page-size"),
                   host: ApiHost = typer.Option(ApiHost.pc, "--host"),
                   settings: Path | None = typer.Option(None, "--settings")) -> None:
    """租出订单列表的一页（只读）。"""
    rt = _rt(settings)
    data = rt.client._post(yc.PATH_LEASE_OUT_LIST,
                           {"gameId": 730, "pageIndex": page, "pageSize": page_size, "sortType": 0, "keywords": ""},
                           base=_dump_host(rt, host), authed=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=1))


@dump_app.command("on-sale")
def dump_on_sale(page: int = typer.Option(1, "--page"), page_size: int = typer.Option(100, "--page-size"),
                 host: ApiHost = typer.Option(ApiHost.pc, "--host"),
                 settings: Path | None = typer.Option(None, "--settings")) -> None:
    """自己在悠悠上架出售中的饰品，一页（只读）。"""
    rt = _rt(settings)
    data = rt.client._post(yc.PATH_MY_ON_SALE, {"pageIndex": page, "pageSize": page_size, "whetherMerge": 0},
                           base=_dump_host(rt, host), authed=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=1))


@dump_app.command("inventory")
def dump_inventory(page_size: int = typer.Option(50, "--page-size"),
                   host: ApiHost = typer.Option(ApiHost.pc, "--host"),
                   settings: Path | None = typer.Option(None, "--settings")) -> None:
    """库存（带悠悠给的参考价）的第一页（只读，不触发库存刷新）。"""
    rt = _rt(settings)
    data = rt.client._post(yc.PATH_INVENTORY_TREND, {"pageIndex": 1, "pageSize": page_size, "IsMerge": 0},
                           base=_dump_host(rt, host), authed=True)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=1))


class ConsoleLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


def _exit_on_sigterm(signum, frame) -> None:
    raise SystemExit(128 + signum)


@app.command()
def run(settings: Path | None = typer.Option(None, "--settings"), no_web: bool = typer.Option(False, "--no-web"),
        web_only: bool = typer.Option(
            False, "--web-only",
            help="只开看板，不启动调度器（不监控、不发简报）。在另一台机器上看看板用，两台都监控会重复推送。"),
        console_level: ConsoleLevel = typer.Option(
            ConsoleLevel.INFO, "--console-level", case_sensitive=False,
            help="控制台日志级别（日志文件照旧 INFO）。launchd 下传 WARNING，launchd.log 只留警告和报错。")) -> None:
    """Start scheduler (+ web dashboard)."""
    if web_only and no_web:
        raise typer.BadParameter("--web-only 和 --no-web 不能同时用", param_hint="--web-only / --no-web")
    import logging
    import os
    import signal
    import sys
    import time

    from skinpulse.config import load_settings
    from skinpulse.runlock import RunLockHeld, acquire_run_lock
    from skinpulse.scheduler import build_scheduler

    from skinpulse.runtime import warn_about_channels

    # 单实例锁必须在建库、配日志、联网之前拿（spec §11）：launchd 拉起的那个和终端里手动跑的
    # 撞车时，后来的这个什么都不碰就退出。launchd 的 KeepAlive 会每 30 秒重试一次。
    try:
        lock = acquire_run_lock(load_settings(settings).data_path / "skinpulse.run.lock")
    except RunLockHeld as e:
        who = f"pid {e.pid}" if e.pid is not None else "pid 未知"
        stop_cmd = ("uv run python -m skinpulse service stop" if sys.platform == "win32"
                    else "scripts/macos/launchagent.sh stop")
        typer.echo(f"已有一个 skinpulse run 在跑（{who}，锁文件 {e.path}），这次不启动。\n"
                   f"如果是开机自启拉起的，用 {stop_cmd} 停掉；"
                   "否则先关掉那个终端里的服务。", err=True)
        raise typer.Exit(1)

    with lock:
        rt = _rt(settings, console_level=console_level.value)
        from skinpulse.db.session import utcnow
        rt.started_at = utcnow()
        from skinpulse.codeversion import code_fingerprint
        rt.code_fingerprint = code_fingerprint()
        # --web-only 不建调度器：recover_pending 也在 build_scheduler 里，看板不会碰在途告警
        sched = None if web_only else build_scheduler(rt)
        if sched is not None:
            warn_about_channels(rt.ntfy)
        # launchctl stop / bootout 发 SIGTERM。默认处理直接杀进程，finally 跑不到；uvicorn 0.52
        # 优雅退出后也会把「原来的」handler 装回去再重发一次信号。所以先装一个抛 SystemExit
        # 的 handler：两条路都会落到 finally。
        previous_sigterm = signal.signal(signal.SIGTERM, _exit_on_sigterm)
        terminated = False
        try:
            if sched is not None:
                sched.start()
            if no_web:
                while True:
                    time.sleep(3600)
            else:
                import uvicorn

                from skinpulse.web.app import create_app

                uvicorn.run(create_app(rt, sched), host=rt.settings.web.host, port=rt.settings.web.port,
                            log_level="warning")
        except SystemExit as e:
            terminated = e.code == 128 + signal.SIGTERM
            raise
        finally:
            if sched is not None:
                sched.shutdown(wait=False)
            signal.signal(signal.SIGTERM, previous_sigterm)
            if terminated:
                # 在跑的那一轮监控在线程池的非守护线程里，解释器正常退出会等它跑完（一轮要几分钟），
                # launchd 默认 20 秒后就 SIGKILL。直接结束：在途的 pending 通知由下次启动时
                # scheduler.build_scheduler 里的 recover_pending 收尾，锁由操作系统释放。
                logging.shutdown()
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(128 + signal.SIGTERM)



@app.command()
def supervise(
    settings: Path | None = typer.Option(None, "--settings"),
    root: Path | None = typer.Option(None, "--root", help="先切到这个目录（仓库根）；注册表 Run 键拉起时没有工作目录"),
) -> None:
    """Windows 守护进程：拉起 run、崩了 30 秒内重拉、接电源时防睡眠。由 `service install` 写的注册表 Run 键调用。"""
    import os

    from skinpulse.config import load_settings
    from skinpulse.supervise import main as supervise_main

    if root is not None:
        # 必须在 load_settings 之前：settings.toml、data/ 都按工作目录解析。这时还不知道日志写哪，
        # 目录不在就直接退出；service status 会查出 Run 值指向的目录不在了（ADR-0023）
        if not root.is_dir():
            raise typer.Exit(2)
        os.chdir(root)
    raise typer.Exit(supervise_main(load_settings(settings).data_path, settings))


def _service(action: str, settings: Path | None) -> None:
    import sys as _sys

    if _sys.platform != "win32":
        typer.echo("service 只用于 Windows；macOS 用 scripts/macos/launchagent.sh "
                   "install | status | stop | start | restart | uninstall", err=True)
        raise typer.Exit(1)
    from skinpulse.config import load_settings
    from skinpulse.service_win import Service, ServiceError

    # load_settings 找不到 settings.toml 会悄悄拷一份示例过来、还会在当前目录建 data/：先拦住
    if settings is None and not Path("settings.toml").exists():
        typer.echo("当前目录没有 settings.toml：在仓库根目录里运行；还没配置的话先跑 "
                   "uv run python -m skinpulse init-config", err=True)
        raise typer.Exit(1)
    svc = Service(root=Path.cwd(), data_dir=load_settings(settings).data_path)
    try:
        getattr(svc, action)()
    except ServiceError as e:
        for line in svc.out:
            typer.echo(line)
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    for line in svc.out:
        typer.echo(line)


_SETTINGS_OPT = typer.Option(None, "--settings")


@service_app.command("install")
def service_install(settings: Path | None = _SETTINGS_OPT) -> None:
    """登录时自动启动（写注册表 Run 键），并马上启动。在仓库根目录里运行。"""
    _service("install", settings)


@service_app.command("status")
def service_status(settings: Path | None = _SETTINGS_OPT) -> None:
    """开机自启装没装、守护进程和服务在不在跑，日志最后 20 行。"""
    _service("status", settings)


@service_app.command("stop")
def service_stop(settings: Path | None = _SETTINGS_OPT) -> None:
    """停掉服务；下次登录会自动恢复。"""
    _service("stop", settings)


@service_app.command("start")
def service_start(settings: Path | None = _SETTINGS_OPT) -> None:
    """stop 之后重新启动。"""
    _service("start", settings)


@service_app.command("restart")
def service_restart(settings: Path | None = _SETTINGS_OPT) -> None:
    """重启（拉了新代码、uv sync 之后用）。"""
    _service("restart", settings)


@service_app.command("uninstall")
def service_uninstall(settings: Path | None = _SETTINGS_OPT) -> None:
    """停掉并删掉开机自启。"""
    _service("uninstall", settings)

@app.command()
def demo(port: int = typer.Option(8799, "--port", help="看板端口（默认 8799，和正式服务错开）"),
         data_dir: Path | None = typer.Option(
             None, "--dir", help="假数据放哪（默认新建一个临时目录；给一个已有的目录会沿用里面的库）"),
         open_browser: bool = typer.Option(True, "--open/--no-open", help="启动后用浏览器打开看板")) -> None:
    """用假数据起一个只读看板，不需要悠悠账号、不联网、不读钥匙串。"""
    import tempfile
    import threading
    import webbrowser

    import uvicorn

    from skinpulse.db.session import utcnow
    from skinpulse.demo import build_demo_runtime, keep_fresh
    from skinpulse.web.app import create_app

    where = data_dir or Path(tempfile.mkdtemp(prefix="skinpulse-demo-"))
    try:
        rt = build_demo_runtime(where, port, now=utcnow())
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"演示看板：{url}\n假数据在 {where}（删掉这个目录即可）。全是虚构的单子和行情，写操作都被拒绝。Ctrl+C 退出。")
    stop = keep_fresh(rt)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        uvicorn.run(create_app(rt, None), host="127.0.0.1", port=port, log_level="warning")
    finally:
        stop.set()

if __name__ == "__main__":
    app()
