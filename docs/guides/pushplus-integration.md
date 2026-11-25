# PushPlus（推送加）接入指南

> 从 SkinPulse 项目的实际接入过程整理（2026-09-23）。目标：让另一个项目**一次接通**，不再踩同样的坑。
> 账号可以共用，**每个项目单独建一个「消息 token」**。

---

## 1. 为什么用它

国内手机上，要「消息按时到、不用给 App 开后台/自启动权限」，借**微信**的通道是最省事的：微信本来就在按时收消息，厂商对它放行。PushPlus 把你的消息以**微信公众号消息**的形式推给你。

对比：ntfy 在国内**实际收不到**——它依赖 Google 的推送（FCM，国内不通），否则 App 得常驻后台；而 ntfy.sh 只存消息 12 小时，App 不打开就永久丢失。SkinPulse 里 ntfy 标成「已发送」的 20 次推送，手机上只看到 2 次。

## 2. 准备 token

1. pushplus.plus 微信扫码登录 →「发送消息」→「消息Token」→「新增消息token」，名字写项目名，便于日后单独删除。
2. **消息 token 和用户 token 都能发消息**（官方原话）。只有 PushPlus 的「开放接口」必须用用户 token，普通发送用不到。
3. 账号需要**实名认证**，否则发送返回 905（同一账号已经做过的话，新 token 直接可用）。
4. **保存 token 的方式**（这里踩过坑）：
   - **不要贴进任何聊天记录**。
   - **Windows 上不要在「输入不回显」的提示框里粘贴**：按 Ctrl+V 有时进来的是一个看不见的控制字符，而不是剪贴板内容，PushPlus 只会回 903「token 不对」，看起来像 token 有问题。**先存进一个文本文件，让程序读文件**，读完删掉文件。
   - 用 PowerShell 5.1 的 `Out-File -Encoding utf8` 写出来的文件，开头带一个看不见的 BOM——读文件时用 `utf-8-sig` 编码去掉它。
   - 程序里存进系统钥匙串（Python 用 `keyring` 库）或环境变量，**不要进仓库**；文件名含 `token` 的 `.txt` 最好在 `.gitignore` 里排除。

## 3. 接口

```
POST https://www.pushplus.plus/send
Content-Type: application/json

{"token": "...", "title": "标题", "content": "正文", "template": "txt"}
```

- 响应信封：`{"code": ..., "msg": ..., "data": ...}`
- **HTTP 永远是 200**，成败只看 `code`。按 HTTP 状态码判断成败是错的。
- 成功时 `data` 是消息流水号（shortCode）。**失败时真正的原因在 `data` 里**，`msg` 只是类别。

| code | 含义 | 怎么处理 |
|---|---|---|
| 200 | 服务端**收下了** | 注意：不等于已送达，见下 |
| 903 | token 不对（实测：乱填 token 时 `data` =「无效的用户token」） | 核对 token；先排查是不是粘贴出了问题 |
| 905 | 账号未实名认证 | 去官网完成认证 |
| 900 | 账号被限制使用（通常是一天请求超过 1000 次，或被封 7 天） | **立即停止请求，绝不重试**。官方说明：继续请求会延长限制，停手约 2 天自动恢复 |
| 999 | 服务端校验错误（实测：不带 token 时 `data` =「token不能为空」） | 看 `data` |
| 888 / 500 / 600 | 积分不足 / 系统错误 / 数据异常 | 见官方返回码文档 |

**模板用 `txt`，不用默认的 `html`**：正文里若出现 `<` 或 `&`，html 模板会把它当标签吃掉或渲染错。`txt` 模板下 `\n` 换行在微信里正常显示，连续两个 `\n` 的空行也在（实测确认）。

## 4. 必须知道的五件事

1. **返回 200 ≠ 送到了微信。** 官方文档原话：同步返回只确认接收，实际发送状态要拿 shortCode 另查。那个查询接口属于「开放接口」，要另外申请 secretKey 并绑 IP 白名单；回调方式则需要一个公网可达的地址。不接这些的话，**上线时发一条测试消息，人在微信里亲眼确认**，是最可靠的验收。
2. **重试只针对「连接阶段」的失败**（`ConnectError` / `ConnectTimeout`）：这类请求一定没到服务端，重试不会造成重复推送。**读超时和 5xx 不要重试**：那时消息可能已经发出、只是响应丢了，重试会让同一条消息推两遍。
3. **900 绝不重试**，理由见上表。
4. **把事件发生时间写进正文**（例如末行「触发于 09-23 00:51」）。任何推送通道都可能延迟，正文里的时间才是事情真正发生的时间，不要依赖通道显示的时间。
5. **token 放请求体，不要拼进 URL**。官方支持把 token 加到 URL 路径上，但 URL 常常会原样出现在日志和异常消息里。同理，记录网络异常时只记异常类型名。

另外，控制发送量：SkinPulse 把一轮里的多条事件**合并成一条消息**，再加一个每小时上限，离 1000 次/天很远。

## 5. 最小参考实现（Python，依赖 httpx）

```python
"""PushPlus 最小客户端（从 SkinPulse 提炼）。"""
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
URL = "https://www.pushplus.plus/send"
_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout)   # 只有连接阶段失败才重试


class PushPlusError(Exception):
    def __init__(self, code, msg="", detail=""):
        super().__init__(f"pushplus {code}: {msg}" + (f"（{detail}）" if detail else ""))
        self.code, self.msg, self.detail = str(code), msg, detail


class PushPlusAuthError(PushPlusError):
    """903：token 不对。"""


class PushPlusNeedsRealName(PushPlusError):
    """905：账号未实名认证。"""


class PushPlusRestricted(PushPlusError):
    """900：账号被限制。立即停止请求，约 2 天恢复——绝不重试。"""


_CODES = {"903": PushPlusAuthError, "905": PushPlusNeedsRealName, "900": PushPlusRestricted}


def send(token: str, title: str, content: str, *, http: httpx.Client | None = None,
         retries: int = 2, backoff: tuple[float, ...] = (3.0, 10.0), sleep=time.sleep) -> str:
    """发一条微信消息，返回 shortCode。返回成功只代表 PushPlus 收下了，不代表已送达微信。"""
    http = http or httpx.Client(timeout=20)
    payload = {"token": token, "title": title, "content": content, "template": "txt"}
    for attempt in range(retries + 1):
        try:
            r = http.post(URL, json=payload)
            break
        except _RETRYABLE as e:
            if attempt >= retries:
                raise
            wait = backoff[min(attempt, len(backoff) - 1)]
            log.warning("pushplus connect failed (%s); retrying in %ss", type(e).__name__, wait)
            sleep(wait)
    try:
        body = r.json()
    except ValueError:
        raise PushPlusError(f"http_{r.status_code}", "响应不是 JSON") from None
    code = str(body.get("code"))
    if code == "200":
        return str(body.get("data") or "")
    raise _CODES.get(code, PushPlusError)(code, str(body.get("msg") or ""), str(body.get("data") or ""))


def read_token_file(path: str | Path) -> str:
    """从文件读 token。utf-8-sig 去掉 PowerShell 写入的 BOM；拒绝空白和控制字符（粘贴出错的典型症状）。"""
    token = Path(path).read_text(encoding="utf-8-sig").strip()
    if not token or any(c.isspace() for c in token) or not all(c.isprintable() for c in token):
        raise ValueError("token 为空，或含空白/控制字符——多半是复制粘贴出了问题")
    return token
```

首次配置建议这样做：**先发一条测试消息，成功了再保存 token**。失败报 903 时，打印程序实际收到的 token **位数**（不要打印 token 本身），方便和网页上的对照：

```python
token = read_token_file("pushplus_token.txt")
try:
    code = send(token, "通道测试", "看到这条说明通了。\n\n这一行上面应该有一个空行。")
except PushPlusAuthError:
    print(f"903：程序收到的 token 共 {len(token)} 位，请与网页上「复制token」的结果对照")
    raise
# 成功后再存：keyring.set_password("<你的项目名>", "pushplus_token", token)
# 然后删掉 pushplus_token.txt
```

## 6. 验收清单

- [ ] 测试消息在**微信里亲眼看到**（不是只看接口返回 200）
- [ ] 正文里的空行显示正常
- [ ] 故意填错 token，程序能识别为 903 并给出可读的提示
- [ ] 日志和异常消息里搜不到 token
- [ ] 用来导入 token 的文本文件已删除，且从未进过 git
