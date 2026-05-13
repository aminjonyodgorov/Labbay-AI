import os
import secrets
import logging
import traceback
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import db

logger = logging.getLogger(__name__)

PAGE_SIZE = 50

app = FastAPI(title="Labbay-AI Admin")

_secret = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=_secret, max_age=86400 * 7)


@app.exception_handler(Exception)
async def all_exceptions(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error(f"Unhandled error on {request.url.path}:\n{tb}")
    return PlainTextResponse(
        f"500 Internal Server Error\n\nPath: {request.url.path}\n\n{tb}",
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


@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/admin")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authed(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/admin/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    expected = os.getenv("ADMIN_PASSWORD", "")
    if not expected:
        return templates.TemplateResponse("login.html",
            {"request": request, "error": "ADMIN_PASSWORD env variable o'rnatilmagan"})
    if not secrets.compare_digest(password, expected):
        return templates.TemplateResponse("login.html",
            {"request": request, "error": "Parol noto'g'ri"})
    request.session["auth"] = True
    return RedirectResponse("/admin", status_code=303)


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
        raise HTTPException(401)
    blocked = await db.is_user_blocked(user_id)
    await db.set_blocked(user_id, not blocked)
    referer = request.headers.get("referer", "/admin/users")
    return RedirectResponse(referer, status_code=303)


@app.get("/admin/transcriptions", response_class=HTMLResponse)
async def transcriptions_page(request: Request, q: str = "", offset: int = 0):
    redirect = require_auth(request)
    if redirect:
        return redirect
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
    items = await db.list_transcriptions(limit=PAGE_SIZE, offset=offset, search=q, errors_only=True)
    total = await db.count_transcriptions(search=q, errors_only=True)
    return templates.TemplateResponse("transcriptions.html", {
        "request": request, "active": "errors",
        "items": items, "total": total, "search": q,
        "errors_only": True, "limit": PAGE_SIZE, "offset": offset,
    })
