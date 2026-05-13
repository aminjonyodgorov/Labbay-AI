import os
import secrets
import logging
import asyncio
from time import monotonic
from collections import defaultdict
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import db

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SEC = 900
LOGIN_LOCKOUT_SEC = 900
LOGIN_FAIL_DELAY_SEC = 1.5

_session_secret = os.getenv("SESSION_SECRET")
if not _session_secret:
    _session_secret = secrets.token_hex(32)
    logger.warning(
        "SESSION_SECRET env var is not set. Generated a random one — sessions "
        "will not survive a restart. Set SESSION_SECRET in Railway Variables."
    )

_login_attempts: dict[str, list[float]] = defaultdict(list)
_login_lockouts: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_login_allowed(ip: str) -> tuple[bool, int]:
    now = monotonic()
    locked_until = _login_lockouts.get(ip, 0)
    if locked_until > now:
        return False, int(locked_until - now)
    _login_attempts[ip] = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SEC]
    if len(_login_attempts[ip]) >= LOGIN_MAX_ATTEMPTS:
        _login_lockouts[ip] = now + LOGIN_LOCKOUT_SEC
        return False, LOGIN_LOCKOUT_SEC
    return True, 0


def _record_failed_login(ip: str) -> None:
    _login_attempts[ip].append(monotonic())


def _reset_login_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)
    _login_lockouts.pop(ip, None)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), interest-cohort=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        return response


class CSRFRefererMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            referer = request.headers.get("referer", "")
            origin = request.headers.get("origin", "")
            host = request.headers.get("host", "")
            if host:
                expected_prefixes = (f"https://{host}", f"http://{host}")
                ok = (
                    (referer and referer.startswith(expected_prefixes))
                    or (origin and origin.startswith(expected_prefixes))
                )
                if not ok:
                    logger.warning(f"CSRF rejected: path={request.url.path} ref={referer!r} origin={origin!r}")
                    return PlainTextResponse("Forbidden", status_code=403)
        return await call_next(request)


app = FastAPI(title="Labbay-AI Admin", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFRefererMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    max_age=86400 * 7,
    https_only=True,
    same_site="lax",
    session_cookie="labbay_session",
)


@app.exception_handler(Exception)
async def all_exceptions(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url.path}: {exc!r}", exc_info=True)
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Xatolik</title>"
        "<div style='font-family:sans-serif;max-width:480px;margin:80px auto;color:#475569;text-align:center'>"
        "<h1 style='color:#0f172a'>500</h1>"
        "<p>Server xatosi yuz berdi.</p></div>",
        status_code=500,
    )


templates = Jinja2Templates(directory="templates")
templates.env.filters["min"] = min
templates.env.filters["max"] = max


def is_authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_auth(request: Request):
    if not is_authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    return None


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/admin", status_code=303)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authed(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/admin/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    ip = _client_ip(request)
    allowed, retry_in = _check_login_allowed(ip)
    if not allowed:
        mins = max(1, retry_in // 60)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Juda ko'p urinish. {mins} daqiqadan keyin qayta urinib ko'ring."},
            status_code=429,
        )

    expected = os.getenv("ADMIN_PASSWORD", "")
    if not expected:
        logger.error("ADMIN_PASSWORD env var is not set")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Server konfiguratsiya xatosi"},
            status_code=500,
        )

    if not secrets.compare_digest(password, expected):
        _record_failed_login(ip)
        await asyncio.sleep(LOGIN_FAIL_DELAY_SEC)
        logger.warning(f"Failed admin login attempt from {ip}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Parol noto'g'ri"},
            status_code=401,
        )

    _reset_login_attempts(ip)
    request.session.clear()
    request.session["auth"] = True
    request.session["session_id"] = secrets.token_hex(16)
    logger.info(f"Admin login success from {ip}")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/logout")
@app.get("/admin/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request):
    redirect = require_auth(request)
    if redirect:
        return redirect
    stats = await db.stats_overview()
    activity = await db.hourly_activity(24)
    recent = await db.list_transcriptions(limit=10, offset=0)

    chart_labels = [r["hour"].strftime("%H:00") for r in activity]
    chart_counts = [r["cnt"] for r in activity]
    chart_errors = [r["errors"] for r in activity]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active": "dashboard",
        "stats": stats,
        "recent": recent,
        "chart_labels": chart_labels,
        "chart_counts": chart_counts,
        "chart_errors": chart_errors,
    })


@app.get("/admin/users", response_class=HTMLResponse)
async def users_page(request: Request, q: str = "", offset: int = 0):
    redirect = require_auth(request)
    if redirect:
        return redirect
    offset = max(0, min(offset, 1_000_000))
    q = q[:200]
    users = await db.list_users(limit=PAGE_SIZE, offset=offset, search=q)
    total = await db.count_users(search=q)
    return templates.TemplateResponse("users.html", {
        "request": request, "active": "users",
        "users": users, "total": total, "search": q,
        "limit": PAGE_SIZE, "offset": offset,
    })


@app.post("/admin/users/{user_id}/toggle")
async def toggle_user(request: Request, user_id: int):
    if not is_authed(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    blocked = await db.is_user_blocked(user_id)
    await db.set_blocked(user_id, not blocked)
    logger.info(f"Admin toggled user {user_id}: blocked={not blocked}")
    return RedirectResponse("/admin/users", status_code=303)


@app.get("/admin/transcriptions", response_class=HTMLResponse)
async def transcriptions_page(request: Request, q: str = "", offset: int = 0):
    redirect = require_auth(request)
    if redirect:
        return redirect
    offset = max(0, min(offset, 1_000_000))
    q = q[:200]
    items = await db.list_transcriptions(limit=PAGE_SIZE, offset=offset, search=q)
    total = await db.count_transcriptions(search=q)
    return templates.TemplateResponse("transcriptions.html", {
        "request": request, "active": "transcriptions",
        "items": items, "total": total, "search": q,
        "errors_only": False, "limit": PAGE_SIZE, "offset": offset,
    })


@app.get("/admin/errors", response_class=HTMLResponse)
async def errors_page(request: Request, q: str = "", offset: int = 0):
    redirect = require_auth(request)
    if redirect:
        return redirect
    offset = max(0, min(offset, 1_000_000))
    q = q[:200]
    items = await db.list_transcriptions(limit=PAGE_SIZE, offset=offset, search=q, errors_only=True)
    total = await db.count_transcriptions(search=q, errors_only=True)
    return templates.TemplateResponse("transcriptions.html", {
        "request": request, "active": "errors",
        "items": items, "total": total, "search": q,
        "errors_only": True, "limit": PAGE_SIZE, "offset": offset,
    })
