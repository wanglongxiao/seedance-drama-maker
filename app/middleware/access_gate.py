# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.
"""云端 Web UI 准入密码中间件。

设计目标：
- 仅当环境变量 ACCESS_PASSWORD 存在且非空时启用（云端在 .env / 函数环境变量中设置具体密码）。
- 本地开发默认不设置该变量 => 中间件完全放行，行为与之前一致。
- 通过一个轻量登录页 + HMAC 签名 Cookie 完成校验，无需引入额外依赖或外部会话存储。
- 不改变任何业务逻辑；仅在请求进入路由前做准入判断。
"""

import hashlib
import hmac
import os
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response, JSONResponse

# Cookie 名称与有效期（秒）
_COOKIE_NAME = "dm_access"
_COOKIE_MAX_AGE = 12 * 60 * 60  # 12 小时

# 无需鉴权即可访问的路径（登录页自身、健康检查、favicon）。
_PUBLIC_PATHS = {"/__login", "/__auth", "/favicon.ico", "/health", "/healthz"}


def _access_password() -> str:
    """读取准入密码；为空表示未启用（放行）。"""
    return (os.getenv("ACCESS_PASSWORD") or "").strip()


def _secret_key() -> bytes:
    """Cookie 签名密钥：优先用独立的 ACCESS_SECRET，否则从密码派生（保证部署即可用）。"""
    secret = (os.getenv("ACCESS_SECRET") or "").strip()
    if not secret:
        secret = "dm-access::" + _access_password()
    return secret.encode("utf-8")


def _sign(expires_at: int) -> str:
    msg = str(expires_at).encode("utf-8")
    sig = hmac.new(_secret_key(), msg, hashlib.sha256).hexdigest()
    return f"{expires_at}.{sig}"


def _valid_cookie(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    raw_exp, _, sig = token.partition(".")
    try:
        expires_at = int(raw_exp)
    except ValueError:
        return False
    if expires_at < int(time.time()):
        return False
    expected = hmac.new(_secret_key(), raw_exp.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _issue_cookie_value() -> str:
    return _sign(int(time.time()) + _COOKIE_MAX_AGE)


def ws_authorized(websocket) -> bool:
    """WebSocket 准入校验：未启用密码时放行；启用时要求有效准入 Cookie。"""
    if not _access_password():
        return True
    return _valid_cookie(websocket.cookies.get(_COOKIE_NAME))


def _login_page(error: bool = False) -> str:
    hint = (
        '<p style="color:#ff4d4f;margin:0 0 12px;font-size:13px;">密码错误，请重试 / Wrong password</p>'
        if error
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>访问验证 / Access</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    background:linear-gradient(135deg,#1f1c2c 0%,#928dab 100%); height:100vh;
    display:flex; align-items:center; justify-content:center; }}
  .card {{ background:#fff; padding:32px 28px; border-radius:16px; width:320px;
    box-shadow:0 12px 40px rgba(0,0,0,.25); }}
  h1 {{ font-size:18px; margin:0 0 4px; color:#222; }}
  p.sub {{ margin:0 0 20px; font-size:13px; color:#888; }}
  input {{ width:100%; padding:12px 14px; border:1px solid #ddd; border-radius:10px;
    font-size:15px; margin-bottom:14px; }}
  input:focus {{ outline:none; border-color:#7b6cf6; }}
  button {{ width:100%; padding:12px; border:none; border-radius:10px; cursor:pointer;
    background:linear-gradient(135deg,#7b6cf6,#5b8def); color:#fff; font-size:15px; font-weight:600; }}
  button:hover {{ opacity:.92; }}
</style>
</head>
<body>
  <form class="card" method="POST" action="/__auth">
    <h1>🎬 访问验证</h1>
    <p class="sub">请输入访问密码 / Enter access password</p>
    {hint}
    <input type="password" name="password" placeholder="Access password" autofocus autocomplete="current-password">
    <input type="hidden" name="next" value="/">
    <button type="submit">进入 / Enter</button>
  </form>
</body>
</html>"""


class AccessGateMiddleware(BaseHTTPMiddleware):
    """当 ACCESS_PASSWORD 已设置时，对所有请求做 Cookie 准入校验。"""

    async def dispatch(self, request: Request, call_next):
        password = _access_password()
        # 未配置密码 => 完全放行（本地开发场景）
        if not password:
            return await call_next(request)

        path = request.url.path

        # 登录页 / 鉴权提交端点
        if path == "/__login":
            return HTMLResponse(_login_page())
        if path == "/__auth" and request.method == "POST":
            return await self._handle_auth(request)
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # 已持有有效 Cookie => 放行
        if _valid_cookie(request.cookies.get(_COOKIE_NAME)):
            return await call_next(request)

        # 未通过校验：WebSocket / API 返回 401；页面重定向到登录页
        if path == "/ws":
            return Response(status_code=401)
        accept = request.headers.get("accept", "")
        if path.startswith("/api") or path.startswith("/static") or "text/html" not in accept:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return HTMLResponse(_login_page())

    async def _handle_auth(self, request: Request) -> Response:
        form = await request.form()
        supplied = (form.get("password") or "").strip()
        next_url = form.get("next") or "/"
        if hmac.compare_digest(supplied, _access_password()):
            resp = RedirectResponse(url=next_url, status_code=303)
            resp.set_cookie(
                _COOKIE_NAME,
                _issue_cookie_value(),
                max_age=_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return resp
        return HTMLResponse(_login_page(error=True), status_code=401)
