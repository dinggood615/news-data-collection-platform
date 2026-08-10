from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import secrets
from datetime import datetime
from urllib.parse import urlencode, urlparse
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import parse_qs

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .database import backup_database, connect, init_db, now_text, set_setting, setting
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
    if request.url.path in {"/healthz", "/wecom/callback", "/telegram/callback"} or request.url.path.startswith("/static/"):
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
        sources = db.execute("SELECT * FROM news_sources ORDER BY id").fetchall()
        topics = db.execute("SELECT * FROM topic_terms ORDER BY topic").fetchall()
        items = db.execute("SELECT * FROM news_items ORDER BY first_seen_at DESC LIMIT 80").fetchall()
        runs = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10").fetchall()
        total_news = db.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        today_news = db.execute(
            "SELECT COUNT(*) FROM news_items WHERE date(first_seen_at)=date('now','localtime')"
        ).fetchone()[0]
        return {"sources": sources,
                "topics": topics,
                "items": items,
                "runs": runs,
                "enabled_sources": sum(1 for source in sources if source["enabled"]),
                "enabled_topics": sum(1 for topic in topics if topic["enabled"]),
                "total_news": total_news,
                "today_news": today_news,
                "schedule": setting("schedule", "08:00"),
                "wecom_configured": bool(setting("wecom_webhook", secret=True)),
                "translation_mode": setting("translation_mode", "argos"),
                "wecom_message": setting("wecom_message"),
                "wecom_app_configured": all((setting("wecom_corp_id", secret=True), setting("wecom_callback_token", secret=True),
                                             setting("wecom_encoding_aes_key", secret=True), setting("wecom_public_url", ""),
                                             setting("wecom_admin_users", ""))),
                "wecom_callback_url": _wecom_callback_url(),
                "wecom_callback_token_value": setting("wecom_callback_token", secret=True),
                "wecom_encoding_aes_key_value": setting("wecom_encoding_aes_key", secret=True),
                "telegram_pending_user": setting("telegram_pending_user"),
                "assistant_message": setting("assistant_message")}


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


def run_assistant_command(message: str) -> str:
    """A deliberately small, allow-listed operations chat entry point."""
    text = message.strip()
    if not text:
        reply = "请输入操作，例如：查看平台状态、立即采集、查看最近新闻、备份数据库。"
    elif any(word in text for word in ("状态", "健康", "status", "health")):
        with connect() as db:
            sources = db.execute("SELECT COUNT(*) FROM news_sources WHERE enabled=1").fetchone()[0]
            latest = db.execute("SELECT status, started_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        detail = f"最近任务：{latest['status']}（{latest['started_at']}）" if latest else "尚无运行记录"
        reply = f"平台在线；已启用新闻源 {sources} 个。{detail}。"
    elif any(word in text for word in ("采集", "collect", "抓取")):
        scheduler.add_job(run_news, id="manual-news", replace_existing=True)
        reply = "已提交一次新闻采集任务，请稍后在“最近运行”查看结果。"
    elif any(word in text for word in ("最近新闻", "最新新闻", "latest news")):
        with connect() as db:
            rows = db.execute("SELECT translated_title,title FROM news_items ORDER BY first_seen_at DESC LIMIT 3").fetchall()
        reply = "最近新闻：" + ("；".join((row["translated_title"] or row["title"])[:70] for row in rows) if rows else "暂无已入库新闻。")
    elif any(word in text for word in ("备份", "backup")):
        path = backup_database(int(setting("backup_retention_days", "14")))
        reply = f"数据库备份已创建：{path.name}。"
    else:
        reply = "仅支持受限操作：查看平台状态、立即采集、查看最近新闻、备份数据库。"
    return reply


@app.post("/assistant")
def assistant_command(message: str = Form(...)):
    set_setting("assistant_message", run_assistant_command(message))
    return RedirectResponse("/", 303)


def _wecom_crypto():
    from wechatpy.enterprise.crypto import WeChatCrypto

    corp_id = setting("wecom_corp_id", secret=True)
    token = setting("wecom_callback_token", secret=True)
    aes_key = setting("wecom_encoding_aes_key", secret=True)
    if not all((corp_id, token, aes_key)):
        raise RuntimeError("企业微信自建应用回调尚未配置")
    return WeChatCrypto(token, aes_key, corp_id)


def _wecom_callback_url() -> str:
    public_url = setting("wecom_public_url", "").rstrip("/")
    return f"{public_url}/wecom/callback" if public_url else ""


def _new_wecom_aes_key() -> str:
    """Enterprise WeChat expects a 43-character base64 AES key (without =)."""
    return base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _wecom_sender_allowed(user_id: str) -> bool:
    allowed = {item.strip() for item in setting("wecom_admin_users", "").replace("，", ",").split(",") if item.strip()}
    return bool(allowed) and user_id in allowed


@app.get("/wecom/callback")
def verify_wecom_callback(request: Request):
    try:
        crypto = _wecom_crypto()
        args = request.query_params
        echo = crypto.check_signature(args["msg_signature"], args["timestamp"], args["nonce"], args["echostr"])
        return PlainTextResponse(echo)
    except Exception:
        return PlainTextResponse("invalid callback", 403)


@app.post("/wecom/callback")
async def receive_wecom_message(request: Request):
    """Receive encrypted Enterprise WeChat messages and only run approved actions."""
    try:
        from wechatpy.enterprise import create_reply, parse_message

        crypto = _wecom_crypto()
        args = request.query_params
        decrypted = crypto.decrypt_message(await request.body(), args["msg_signature"], args["timestamp"], args["nonce"])
        message = parse_message(decrypted)
        if getattr(message, "type", "") != "text":
            reply_text = "仅支持文本指令：状态、采集、最近新闻、备份。"
        elif not _wecom_sender_allowed(message.source):
            reply_text = "当前账号未获授权使用运维助手。"
        else:
            reply_text = run_assistant_command(message.content)
        reply_xml = create_reply(reply_text, message).render()
        encrypted = crypto.encrypt_message(reply_xml, args["nonce"], args["timestamp"])
        return Response(encrypted, media_type="application/xml")
    except Exception:
        return PlainTextResponse("invalid callback", 403)

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


@app.post("/wecom/app-settings")
def save_wecom_app_settings(corp_id: str = Form(""), agent_id: str = Form(""), app_secret: str = Form(""),
                            callback_token: str = Form(""), encoding_aes_key: str = Form(""), admin_users: str = Form("")):
    values = (("wecom_corp_id", corp_id, True), ("wecom_agent_id", agent_id, False),
              ("wecom_app_secret", app_secret, True), ("wecom_callback_token", callback_token, True),
              ("wecom_encoding_aes_key", encoding_aes_key, True))
    for key, value, secret in values:
        if value.strip():
            set_setting(key, value.strip(), secret=secret)
    if admin_users.strip():
        set_setting("wecom_admin_users", admin_users.strip())
    set_setting("wecom_message", "企业微信自建应用配置已保存；请在企业微信后台验证回调地址。")
    return RedirectResponse("/", 303)


@app.post("/wecom/quick-settings")
def save_wecom_quick_settings(corp_id: str = Form(""), public_url: str = Form(""), admin_users: str = Form("")):
    """Save the minimum self-built-app settings and generate callback secrets."""
    if corp_id.strip():
        set_setting("wecom_corp_id", corp_id.strip(), secret=True)
    if public_url.strip():
        parsed = urlparse(public_url.strip())
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
            set_setting("wecom_message", "企业微信公网地址应为有效 HTTPS 根地址，例如 https://news.example.com。")
            return RedirectResponse("/", 303)
        set_setting("wecom_public_url", public_url.strip().rstrip("/"))
    if admin_users.strip():
        users = [item.strip() for item in admin_users.replace("，", ",").split(",") if item.strip()]
        set_setting("wecom_admin_users", ",".join(dict.fromkeys(users)))
    if not setting("wecom_callback_token", secret=True):
        set_setting("wecom_callback_token", secrets.token_hex(16), secret=True)
    if not setting("wecom_encoding_aes_key", secret=True):
        set_setting("wecom_encoding_aes_key", _new_wecom_aes_key(), secret=True)
    set_setting("wecom_message", "企业微信快速配置已保存。复制下方三项到企业微信自建应用的“接收消息”页面后点击保存验证。")
    return RedirectResponse("/", 303)


@app.post("/wecom/check")
def check_wecom_setup():
    missing = []
    if not setting("wecom_corp_id", secret=True): missing.append("CorpID")
    if not setting("wecom_public_url", ""): missing.append("HTTPS 公网地址")
    if not setting("wecom_admin_users", ""): missing.append("管理员 UserID")
    if not setting("wecom_callback_token", secret=True): missing.append("Token")
    if not setting("wecom_encoding_aes_key", secret=True): missing.append("EncodingAESKey")
    if missing:
        set_setting("wecom_message", "企业微信配置尚不完整：" + "、".join(missing))
    else:
        try:
            _wecom_crypto()
            set_setting("wecom_message", "配置已就绪：将下方回调地址、Token、EncodingAESKey 粘贴到企业微信自建应用并保存验证；随后发送“状态”即可。")
        except Exception:
            set_setting("wecom_message", "本地回调参数校验失败，请重新保存企业微信快速配置。")
    return RedirectResponse("/", 303)


@app.post("/telegram/settings")
def save_telegram_settings(bot_token: str = Form(""), public_url: str = Form("")):
    if bot_token.strip():
        set_setting("telegram_bot_token", bot_token.strip(), secret=True)
    if not setting("telegram_webhook_secret", secret=True):
        set_setting("telegram_webhook_secret", secrets.token_urlsafe(24), secret=True)
    if public_url.strip():
        parsed = urlparse(public_url.strip())
        if parsed.scheme == "https" and parsed.netloc:
            set_setting("telegram_public_url", public_url.strip().rstrip("/"))
    set_setting("wecom_message", "Telegram 配置已保存；请将 Webhook 指向 /telegram/callback。")
    return RedirectResponse("/", 303)


@app.post("/telegram/bind-pending")
def bind_pending_telegram_user():
    pending = setting("telegram_pending_user", "")
    if not pending:
        set_setting("wecom_message", "暂时没有待绑定的 Telegram 用户，请先在机器人中发送 /start。")
    else:
        allowed = {item.strip() for item in setting("telegram_admin_users", "").replace("，", ",").split(",") if item.strip()}
        allowed.add(pending.split("|", 1)[0])
        set_setting("telegram_admin_users", ",".join(sorted(allowed)))
        set_setting("telegram_pending_user", "")
        set_setting("wecom_message", "Telegram 用户已绑定为管理员。")
    return RedirectResponse("/", 303)


@app.post("/telegram/webhook")
def configure_telegram_webhook():
    token = setting("telegram_bot_token", secret=True)
    secret = setting("telegram_webhook_secret", secret=True)
    public_url = setting("telegram_public_url", "").rstrip("/")
    if not token or not secret or not public_url:
        set_setting("wecom_message", "请先保存 Telegram Token、Webhook Secret 和 HTTPS 公网地址。")
        return RedirectResponse("/", 303)
    try:
        body = urlencode({"url": f"{public_url}/telegram/callback", "secret_token": secret, "allowed_updates": json.dumps(["message"])}).encode()
        request = UrlRequest(f"https://api.telegram.org/bot{token}/setWebhook", data=body, method="POST")
        response = json.loads(urlopen(request, timeout=15).read().decode())
        set_setting("wecom_message", "Telegram Webhook 已配置。" if response.get("ok") else "Telegram Webhook 配置失败。")
    except Exception:
        set_setting("wecom_message", "Telegram Webhook 配置失败，请检查公网 HTTPS 和 Token。")
    return RedirectResponse("/", 303)


def _telegram_sender_allowed(user_id: str) -> bool:
    allowed = {item.strip() for item in setting("telegram_admin_users", "").replace("，", ",").split(",") if item.strip()}
    return bool(allowed) and user_id in allowed


@app.post("/telegram/callback")
async def receive_telegram_message(request: Request):
    """Telegram webhook; the secret header and chat-id allowlist gate every operation."""
    expected = setting("telegram_webhook_secret", secret=True)
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        return JSONResponse({"ok": False}, 403)
    try:
        update = await request.json()
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat_id = message.get("chat", {}).get("id")
        sender_id = str(message.get("from", {}).get("id") or "")
        if not chat_id or not text:
            return JSONResponse({"ok": True})
        if _telegram_sender_allowed(sender_id):
            reply = run_assistant_command(text)
        elif text.lower().startswith("/start"):
            username = str(message.get("from", {}).get("username") or message.get("from", {}).get("first_name") or "未知用户")[:60]
            set_setting("telegram_pending_user", f"{sender_id}|{username}")
            reply = "已收到绑定申请，请在平台网页的 Telegram 区域点击“一键批准绑定”。"
        else:
            reply = "当前账号未获授权。请先向机器人发送 /start，并由平台管理员在网页中批准绑定。"
        return JSONResponse({"method": "sendMessage", "chat_id": chat_id, "text": reply})
    except Exception:
        return JSONResponse({"ok": False}, 400)
