from __future__ import annotations

import ipaddress
import logging
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from skinpulse.analysis.pick import PoolError
from skinpulse.services.pick import PickRefused, PickRunner
from skinpulse.web.brief_page import brief_payload
from skinpulse.web.fills import fills_payload
from skinpulse.web.pnl import pnl_items, pnl_payload, pnl_sells
from skinpulse.web.analysis import analysis_payload
from skinpulse.web.item import item_payload
from skinpulse.web.market import market_payload
from skinpulse.web.overview import meta_payload, overview_payload
from skinpulse.web.paused import paused_payload
from skinpulse.web.pick import (
    estimate_payload, filters_from_query, options_payload, run_payload, runs_payload,
)
from skinpulse.web.ratelimit import Throttle
from skinpulse.web.security import SecurityMiddleware, allowed_hosts
from skinpulse.web.settings_api import SettingsError, apply_patch
from skinpulse.web.system import RestartRefused, request_restart, status_payload as system_status
from skinpulse.web.pushplus_token import update_pushplus
from skinpulse.web.youpin_token import TokenError, add_account, patch_account, update_token, validation_client

log = logging.getLogger(__name__)

HERE = Path(__file__).parent


def create_app(rt, sched=None, *, dist_dir: Path | None = None, youpin_factory=None,
               clock=time.monotonic, restart_kill=None, pushplus_factory=None, youpin_new_factory=None,
               buff_factory=None) -> FastAPI:
    """`youpin_factory`：换 token 时用来校验的临时悠悠 client（测试里换成替身）；`clock` 给限流用；
    `restart_kill`：「重启服务」真正发信号的那一步（测试里换成替身）；`pushplus_factory`：录 PushPlus
    token 时建 client 用（测试里换成替身）；`youpin_new_factory`：添加账号时用新设备身份校验的 client（测试里换成替身）；
    `buff_factory(cookie, device_id)`：校验 BUFF session 的 client（测试里换成替身）。"""
    app = FastAPI(title="SkinPulse")
    token_throttle = Throttle(clock=clock)
    pushplus_throttle = Throttle(clock=clock)
    make_youpin = youpin_factory or (lambda account_id=1: validation_client(rt, account_id))

    # Warn if dashboard is bound to a non-loopback address (no authentication).
    host = rt.settings.web.host
    is_loopback = False

    # Check if it's a hostname (treat as non-loopback; will be resolved at runtime).
    if host in ("localhost",):
        is_loopback = True
    else:
        try:
            addr = ipaddress.ip_address(host)
            # IPv4 loopback: 127.0.0.0/8; IPv6 loopback: ::1
            if isinstance(addr, ipaddress.IPv4Address):
                is_loopback = addr in ipaddress.ip_network("127.0.0.0/8")
            else:
                is_loopback = addr == ipaddress.IPv6Address("::1")
        except ValueError:
            # Not a valid IP or known hostname; warn as non-loopback
            is_loopback = False

    if not is_loopback:
        log.warning(
            "Dashboard binding to non-loopback address %s. "
            "The dashboard has no authentication; anything that can reach %s:%d "
            "can change settings and trigger marketplace requests. Note: requests are only accepted "
            "when their Host is loopback or exactly this bind address - a wildcard bind (0.0.0.0/::) "
            "still refuses other machines (web/security.py).",
            host, host, rt.settings.web.port
        )

    # 跨站防护（看板改版 spec §6）：Host 白名单、写请求同源 + 只收 JSON、安全响应头。
    app.add_middleware(SecurityMiddleware, hosts=allowed_hosts(rt.settings.web.port, host))

    if getattr(rt, "demo", False):
        # `skinpulse demo`：只读。参数设置、更新 token、立即同步、开始选品、重启服务一律拒绝。
        @app.middleware("http")
        async def demo_read_only(request: Request, call_next):
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "演示模式不能修改：数据都是假的，接上自己的账号请看 README"},
                                    status_code=403)
            return await call_next(request)


    @app.get("/api/overview")
    def api_overview(account: int | None = None):
        return overview_payload(rt, account_id=account)

    @app.get("/api/meta")
    def api_meta():
        return meta_payload(rt)

    @app.get("/api/item/{template_id}")
    def api_item(template_id: int):
        d = item_payload(rt, template_id)
        if d is None:
            raise HTTPException(404, "未找到这件饰品：库中无它的求购，也无 SteamDT 映射")
        return d

    @app.get("/api/market")
    def api_market():
        return market_payload(rt)

    @app.get("/api/analysis")
    def api_analysis():
        return analysis_payload(rt)

    @app.get("/api/brief")
    def api_brief():
        return brief_payload(rt)

    @app.get("/api/fills")
    def api_fills(days: int = 30, account: int | None = None):
        return fills_payload(rt, days=days, account_id=account)

    @app.get("/api/pnl")
    def api_pnl(account: int | None = None):
        return pnl_payload(rt, account)

    @app.get("/api/pnl/items")
    def api_pnl_items(account: int | None = None, sort: Literal["worst", "best"] = "worst",
                      basis: Literal["market", "net"] = "market", q: str = "", category: str | None = None,
                      offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        return pnl_items(rt, account, sort=sort, basis=basis, q=q[:64], category=category, offset=offset, limit=limit)

    @app.get("/api/pnl/sells")
    def api_pnl_sells(account: int | None = None, limit: int = Query(12, ge=0, le=1000)):
        return pnl_sells(rt, account, limit=limit or None)

    @app.get("/api/settings")
    def api_settings():
        return rt.settings.model_dump(mode="json")

    @app.post("/api/settings")
    def api_settings_update(patch: Any = Body(None)):
        try:
            return apply_patch(rt, sched, patch)
        except SettingsError as e:
            raise HTTPException(422, e.errors) from None

    @app.post("/api/youpin/token")
    def api_youpin_token(body: Any = Body(None)):
        try:
            return update_token(rt, body, factory=make_youpin, throttle=token_throttle, sched=sched)
        except TokenError as e:
            raise HTTPException(e.status, e.detail) from None

    @app.post("/api/pushplus/token")
    def api_pushplus_token(body: Any = Body(None)):
        from skinpulse.adapters.pushplus.client import PushPlusClient
        try:
            return update_pushplus(rt, body, factory=pushplus_factory or PushPlusClient, throttle=pushplus_throttle)
        except TokenError as e:
            raise HTTPException(e.status, e.detail) from None

    @app.post("/api/accounts")
    def api_add_account(body: Any = Body(None)):
        from skinpulse.web.buff_account import add_buff_account
        try:
            if isinstance(body, dict) and body.get("platform") == "buff":
                return add_buff_account(rt, body, throttle=token_throttle, sched=sched, client_factory=buff_factory)
            return add_account(rt, body, factory=make_youpin, throttle=token_throttle, sched=sched,
                               new_client=youpin_new_factory)
        except TokenError as e:
            raise HTTPException(e.status, e.detail) from None

    @app.patch("/api/accounts/{account_id}")
    def api_patch_account(account_id: int, body: Any = Body(None)):
        try:
            return patch_account(rt, account_id, body, sched=sched)
        except TokenError as e:
            raise HTTPException(e.status, e.detail) from None

    @app.post("/api/buff/cookie")
    def api_buff_cookie(body: Any = Body(None)):
        from skinpulse.web.buff_account import update_buff_cookie
        try:
            return update_buff_cookie(rt, body, throttle=token_throttle, sched=sched, client_factory=buff_factory)
        except TokenError as e:
            raise HTTPException(e.status, e.detail) from None

    @app.post("/api/buff/sync")
    def api_buff_sync():
        from skinpulse.web.buff_account import start_buff_round
        # 不在接口里同步：一轮十几次请求、每次隔 6 秒。让下一轮马上开始（BUFF spec §8）
        return {"round_started": start_buff_round(sched)}

    @app.post("/api/youpin/sync")
    def api_sync(body: Any = Body(None)):
        from skinpulse.services.account import sync_my_orders
        # 不给 account_id 就按号依次同步全部启用的号（多账号 spec §7）
        wanted = body.get("account_id") if isinstance(body, dict) else None
        accs = rt.accounts
        if wanted is not None and wanted not in accs:
            raise HTTPException(404, "账号不存在或已停用")
        synced = 0
        # 和每轮监控、暂停单检查错开：同一个 client 不同时发请求（暂停单 spec §3.4）
        lock = getattr(rt, "youpin_lock", None)
        with rt.session_factory() as s, (lock if lock is not None else nullcontext()):
            for aid in ([wanted] if wanted is not None else sorted(accs)):
                synced += sync_my_orders(s, accs[aid])
        return {"synced": synced}

    # 价位三期选品（价位 spec §9b）。PickRunner 建的时候会把上次进程没跑完的标成中断
    pick_runner = PickRunner(rt)

    @app.get("/api/pick/options")
    def api_pick_options():
        return options_payload(rt)

    @app.get("/api/pick/estimate")
    def api_pick_estimate(request: Request):
        try:
            return estimate_payload(rt, filters_from_query(request.query_params))
        except PoolError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/pick/runs")
    def api_pick_runs():
        return runs_payload(rt, pick_runner)

    @app.post("/api/pick/runs")
    def api_pick_start(body: Any = Body(None)):
        try:
            return {"id": pick_runner.start(body)}
        except PickRefused as e:
            raise HTTPException(e.status, e.message) from None

    # 充值前检查暂停单（暂停单 spec §5.3）：只读库；「检查一次」交给调度器在后台跑，请求悠悠的两步和每轮监控错开
    @app.get("/api/paused")
    def api_paused():
        from skinpulse.db.session import utcnow
        return paused_payload(rt, utcnow())

    @app.post("/api/paused/check")
    def api_paused_check(body: Any = Body(None)):
        from skinpulse.db.session import utcnow
        from skinpulse.scheduler import schedule_paused_check
        from skinpulse.services.paused import PausedRefused, start_run

        if sched is None:
            raise HTTPException(409, "此实例未运行监控（测试实例或 --web-only），请在终端使用 check-paused")
        with rt.session_factory() as s:
            try:
                run_id = start_run(s, rt.monitor, "dashboard", utcnow())
            except PausedRefused as e:
                raise HTTPException(e.status, e.message) from None
        schedule_paused_check(sched, rt, run_id)
        return {"run_id": run_id}

    @app.get("/api/pick/runs/{run_id}")
    def api_pick_run(run_id: int):
        out = run_payload(rt, run_id)
        if out is None:
            raise HTTPException(404, "未找到这次选品")
        return {**out, "busy": pick_runner.busy}

    @app.get("/api/status")
    def api_status():
        return system_status(rt)

    @app.post("/api/system/restart")
    def api_restart(body: Any = Body(None)):
        try:
            return request_restart(rt, pick_busy=pick_runner.busy, kill=restart_kill)
        except RestartRefused as e:
            raise HTTPException(e.status, e.message) from None

    # 旧的系统状态页、设置页并进了前端的 /system（三期）；旧书签还能用
    @app.get("/status", include_in_schema=False)
    @app.get("/settings", include_in_schema=False)
    def legacy_system():
        return RedirectResponse("/system", status_code=302)

    _install_spa(app, Path(dist_dir) if dist_dir is not None else HERE / "dist")
    return app


# ---- 前端单页应用（看板改版 spec §2.3）----

NO_BUILD = ("看板前端还没构建（skinpulse/web/dist/index.html 不存在）。\n"
            "正常情况下构建产物已经提交在仓库里；如果你是刚改了前端，运行：\n"
            "    npm --prefix frontend ci && npm --prefix frontend run build\n")


class _ImmutableStatic(StaticFiles):
    """dist/assets 下的文件名带内容哈希，可以放心长缓存。"""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


def _install_spa(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()
    index = dist / "index.html"
    if (dist / "assets").is_dir():
        app.mount("/assets", _ImmutableStatic(directory=dist / "assets"), name="assets")

    def spa_index():
        if not index.is_file():
            return PlainTextResponse(NO_BUILD, status_code=503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})  # CSP 由安全中间件统一加

    @app.get("/", include_in_schema=False)
    def spa_root():
        return spa_index()

    # 必须最后注册：所有 /api/* 和旧地址的跳转先匹配。
    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not found")
        if path:
            f = (dist / path).resolve()
            if f.is_file() and f.is_relative_to(dist):  # 挡住 ../ 越界
                return FileResponse(f)
        return spa_index()
