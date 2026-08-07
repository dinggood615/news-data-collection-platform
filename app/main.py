from __future__ import annotations

import asyncio
import base64
import os
import secrets
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .database import connect, init_db, now_text, set_setting, setting
from .runner import collect_news, send_wecom_test

app = FastAPI(title="新闻数据采集平台")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
SESSION_COOKIE = "news_session"


def serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(os.getenv("APP_SECRET", "change-me"), salt="news-session")


@app.middleware("http")
async def admin_only(request: Request, call_next):
    if request.url.path == "/healthz" or request.url.path.startswith("/static/"):
        return await call_next(request)
    username = setting("admin_username", os.getenv("ADMIN_USERNAME", "admin"))
    password = setting("admin_password", os.getenv("ADMIN_PASSWORD", "admin"), secret=True)
    auth = request.headers.get("authorization", "")
    try:
        scheme, value = auth.split(" ", 1)
        supplied_user, supplied_password = base64.b64decode(value).decode().split(":", 1)
    except Exception:
        scheme, supplied_user, supplied_password = "", "", ""
    basic_ok = scheme.lower() == "basic" and supplied_user == username and secrets.compare_digest(supplied_password, password)
    try:
        session_ok = secrets.compare_digest(serializer().loads(request.cookies.get(SESSION_COOKIE, ""), max_age=28800), username)
    except (BadSignature, TypeError): session_ok = False
    if not basic_ok and not session_ok:
        return PlainTextResponse("需要管理员登录", 401, {"WWW-Authenticate": 'Basic realm="News Platform"'})
    response = await call_next(request)
    if basic_ok: response.set_cookie(SESSION_COOKIE, serializer().dumps(username), max_age=28800, httponly=True, secure=True, samesite="strict")
    return response


def context() -> dict:
    with connect() as db:
        return {"sources": db.execute("SELECT * FROM news_sources ORDER BY id").fetchall(),
                "topics": db.execute("SELECT * FROM topic_terms ORDER BY topic").fetchall(),
                "items": db.execute("SELECT * FROM news_items ORDER BY first_seen_at DESC LIMIT 80").fetchall(),
                "runs": db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10").fetchall(),
                "schedule": setting("schedule", "08:00"),
                "wecom_configured": bool(setting("wecom_webhook", secret=True)),
                "translation_mode": setting("translation_mode", "argos"),
                "wecom_message": setting("wecom_message")}


@app.on_event("startup")
def startup(): init_db(); scheduler.start(); reschedule()

@app.on_event("shutdown")
def shutdown(): scheduler.shutdown(wait=False)

@app.get("/")
def home(request: Request): return templates.TemplateResponse(request, "news.html", context())

@app.get("/healthz")
def healthz():
    try:
        with connect() as db: db.execute("SELECT 1").fetchone()
        return {"status": "ok", "service": "news"}
    except Exception: return JSONResponse({"status": "error"}, 503)


@app.get("/_internal/auth-check", status_code=204)
def auth_check():
    """Nginx auth_request target; authentication is enforced by the middleware."""
    return None


def run_news():
    with connect() as db: run_id = db.execute("INSERT INTO runs(started_at,status) VALUES(?,?)", (now_text(), "running")).lastrowid
    try: collected, new, message = collect_news(); status = "success"
    except Exception as exc: collected, new, status, message = 0, 0, "failed", f"{type(exc).__name__}: {exc}"
    with connect() as db: db.execute("UPDATE runs SET finished_at=?,status=?,collected_count=?,new_count=?,message=? WHERE id=?", (now_text(), status, collected, new, message, run_id))


def reschedule():
    hour, minute = setting("schedule", "08:00").split(":")
    scheduler.add_job(run_news, "cron", hour=int(hour), minute=int(minute), id="daily-news", replace_existing=True)


@app.post("/run")
def run_now(): scheduler.add_job(run_news, id="manual-news", replace_existing=True); return RedirectResponse("/", 303)

@app.post("/sources")
def add_source(name: str = Form(...), url: str = Form(...)):
    if name.strip() and url.startswith(("https://", "http://")):
        with connect() as db: db.execute("INSERT OR IGNORE INTO news_sources(name,url,created_at) VALUES(?,?,?)", (name.strip()[:80], url.strip(), now_text()))
    return RedirectResponse("/", 303)

@app.post("/sources/{source_id}/toggle")
def toggle_source(source_id: int):
    with connect() as db: db.execute("UPDATE news_sources SET enabled=1-enabled WHERE id=?", (source_id,))
    return RedirectResponse("/", 303)

@app.post("/sources/{source_id}/delete")
def delete_source(source_id: int):
    with connect() as db: db.execute("DELETE FROM news_sources WHERE id=?", (source_id,))
    return RedirectResponse("/", 303)

@app.post("/topics/{topic}")
def save_topic(topic: str, terms: str = Form(...), enabled: str = Form("0")):
    with connect() as db: db.execute("UPDATE topic_terms SET terms=?,enabled=? WHERE topic=?", (terms.strip(), 1 if enabled == "1" else 0, topic))
    return RedirectResponse("/", 303)

@app.post("/settings")
def save_settings(schedule: str = Form(...), wecom_webhook: str = Form(""), translation_mode: str = Form("argos")):
    set_setting("schedule", schedule); set_setting("translation_mode", translation_mode)
    if wecom_webhook.strip():
        parsed = urlparse(wecom_webhook.strip())
        valid_webhook = (parsed.scheme == "https" and parsed.netloc == "qyapi.weixin.qq.com"
                         and parsed.path == "/cgi-bin/webhook/send" and bool(parse_qs(parsed.query).get("key")))
        if not valid_webhook:
            set_setting("wecom_message", "Webhook 地址格式错误。请在企业微信机器人详情中复制 qyapi.weixin.qq.com 的 Webhook 地址。")
            return RedirectResponse("/", 303)
        set_setting("wecom_webhook", wecom_webhook.strip(), secret=True)
        set_setting("wecom_message", "企业微信 Webhook 已保存，可点击下方按钮发送测试消息。")
    reschedule(); return RedirectResponse("/", 303)


@app.post("/wecom/test")
def test_wecom():
    try:
        set_setting("wecom_message", send_wecom_test())
    except Exception as exc:
        set_setting("wecom_message", f"测试发送失败：{type(exc).__name__}")
    return RedirectResponse("/", 303)
