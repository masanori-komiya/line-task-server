import hmac
import hashlib
import base64
import collections
import os
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

SESSION_COOKIE = "admin_session"
SESSION_MAX_AGE = 24 * 3600  # 24 hours

_LOCKOUT_WINDOW = 15 * 60  # 15 minutes
_MAX_ATTEMPTS = 10
_failed_attempts: dict[str, list[float]] = collections.defaultdict(list)

SECURE_COOKIE = os.getenv("SECURE_COOKIE", "true").lower() != "false"

_AUTO_SECRET_KEY: bytes = os.urandom(32)


def _secret_key() -> bytes:
    key = os.getenv("ADMIN_SECRET_KEY")
    if not key:
        return _AUTO_SECRET_KEY
    return key.encode()


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if now - t < _LOCKOUT_WINDOW]
    return len(_failed_attempts[ip]) >= _MAX_ATTEMPTS


def record_failed_attempt(ip: str) -> None:
    _failed_attempts[ip].append(time.time())


def reset_attempts(ip: str) -> None:
    _failed_attempts.pop(ip, None)


def create_session_token(username: str) -> str:
    ts = str(int(time.time()))
    payload = f"{username}:{ts}"
    sig = hmac.new(_secret_key(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def verify_session_token(token: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        parts = raw.split(":")
        if len(parts) != 3:
            return False
        username, ts, sig = parts
        if int(time.time()) - int(ts) > SESSION_MAX_AGE:
            return False
        payload = f"{username}:{ts}"
        expected_sig = hmac.new(_secret_key(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def check_credentials(username: str, password: str) -> bool:
    admin_user = os.getenv("ADMIN_USERNAME", "").strip()
    admin_pass = os.getenv("ADMIN_PASSWORD", "").strip()
    if not admin_user or not admin_pass:
        return False
    return (
        hmac.compare_digest(username, admin_user)
        and hmac.compare_digest(password, admin_pass)
    )


class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/admin") and path not in ("/admin/login",):
            token = request.cookies.get(SESSION_COOKIE)
            if not token or not verify_session_token(token):
                return RedirectResponse(url="/admin/login", status_code=302)
        return await call_next(request)
