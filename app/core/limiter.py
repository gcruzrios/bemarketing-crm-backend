from slowapi import Limiter
from starlette.requests import Request


def _key(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _key_user_or_ip(request: Request) -> str:
    auth = request.headers.get("x-api-key") or request.headers.get("authorization", "")[-24:]
    return f"{_key(request)}:{auth}" if auth else _key(request)


limiter = Limiter(key_func=_key_user_or_ip, default_limits=["300/minute"])
