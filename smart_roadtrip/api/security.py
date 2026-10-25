"""API 鉴权、用户身份与进程内滑动窗口限流。

``API_KEY`` / ``API_KEYS`` 都为空时视为开发模式，不校验 key；限流默认
60 次/分钟，``RATE_LIMIT_PER_MINUTE=0`` 可关闭。``API_KEYS`` 支持逗号
分隔的多用户 key，每个 key 的哈希作为线程 owner，实现简单租户隔离。
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from smart_roadtrip import config

_WINDOW_SECONDS = 60.0
_MAX_IDENTITIES = 2048
_ANONYMOUS_OWNER = "anonymous"

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def configured_api_key_entries() -> list[tuple[str, int | None]]:
    """解析 ``API_KEYS``，支持 ``key`` 或 ``key:24h_limit``。"""

    entries: list[tuple[str, int | None]] = []
    for item in config.API_KEYS.split(","):
        item = item.strip()
        if not item:
            continue
        key, separator, limit_text = item.rpartition(":")
        if separator and limit_text.isdigit():
            entries.append((key.strip(), max(0, int(limit_text))))
        else:
            entries.append((item, None))
    if config.API_KEY:
        entries.append((config.API_KEY, None))
    return entries


def configured_api_keys() -> list[str]:
    return [key for key, _ in configured_api_key_entries()]


def get_owner_limit(request: Request) -> int:
    """返回当前 key 的 24 小时配额；未单独配置时使用全局默认。"""

    default = config.TENANT_MAX_THREADS_PER_DAY
    provided = request.headers.get(config.API_KEY_HEADER, "")
    if not provided:
        return default
    for key, limit in configured_api_key_entries():
        if secrets.compare_digest(provided, key):
            return default if limit is None else limit
    return default


def require_api_key(request: Request) -> None:
    """FastAPI 依赖：配置了 key 时校验请求头。"""

    keys = configured_api_keys()
    if not keys:
        return

    provided = request.headers.get(config.API_KEY_HEADER, "")
    if not provided or not any(
        secrets.compare_digest(provided, key) for key in keys
    ):
        raise HTTPException(status_code=401, detail="无效或缺失的 API key。")


def require_admin_key(request: Request) -> None:
    """FastAPI 依赖：校验管理员 key；未配置管理员接口时返回 403。"""

    if not config.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="管理员接口未启用。")

    provided = request.headers.get(config.ADMIN_API_KEY_HEADER, "")
    if not provided or not secrets.compare_digest(
        provided, config.ADMIN_API_KEY
    ):
        raise HTTPException(
            status_code=401, detail="无效或缺失的管理员 key。"
        )


def get_owner(request: Request) -> str:
    """返回当前请求的 owner；开发模式为 ``anonymous``。

    配置了 key 但请求头无效/缺失时返回空字符串，调用方应拒绝访问。
    """

    keys = configured_api_keys()
    if not keys:
        return _ANONYMOUS_OWNER

    provided = request.headers.get(config.API_KEY_HEADER, "")
    if not provided or not any(
        secrets.compare_digest(provided, key) for key in keys
    ):
        return ""
    return _owner_for_key(provided)


def reset_rate_limiter() -> None:
    with _lock:
        _hits.clear()


def _owner_for_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _identity(request: Request) -> str:
    value = (
        request.headers.get(config.API_KEY_HEADER, "")
        or request.headers.get(config.ADMIN_API_KEY_HEADER, "")
    )
    if value:
        return f"key:{_owner_for_key(value)}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _is_limited(path: str) -> bool:
    return (
        path.startswith("/chat")
        or path.startswith("/trips")
        or path.startswith("/admin")
    )


def _allow(identity: str) -> bool:
    limit = config.RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return True

    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    with _lock:
        queue = _hits[identity]
        while queue and queue[0] <= cutoff:
            queue.popleft()
        if len(queue) >= limit:
            return False
        queue.append(now)

        if len(_hits) > _MAX_IDENTITIES:
            for key in [name for name, values in _hits.items() if not values]:
                del _hits[key]
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if _is_limited(request.url.path) and not _allow(_identity(request)):
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试。"},
                headers={"Retry-After": "60"},
            )
        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if _is_limited(request.url.path):
            raw_length = request.headers.get("content-length")
            if raw_length:
                try:
                    length = int(raw_length)
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Content-Length 无效。"},
                    )
                if length > config.MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "请求体过大。"},
                    )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
