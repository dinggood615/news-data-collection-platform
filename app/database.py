from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import json
import secrets
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def db_path() -> str:
    return os.getenv("DATABASE_PATH", "/data/news.sqlite3")


@contextmanager
def connect():
    db = sqlite3.connect(db_path(), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


DEFAULT_SOURCES = (
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("DW World", "https://rss.dw.com/rdf/rss-en-world"),
    ("France 24", "https://www.france24.com/en/rss"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("The Guardian World", "https://www.theguardian.com/world/rss"),
    ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
    ("CBC World", "https://www.cbc.ca/webfeed/rss/rss-world"),
    ("Sky News World", "https://feeds.skynews.com/feeds/rss/world.xml"),
    ("重大新闻（BBC）", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("重大新闻（美联社）", "https://feeds.apnews.com/apnews/topnews"),
    ("商业新闻（BBC）", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("商业新闻（DW）", "https://rss.dw.com/rdf/rss-en-bus"),
    # Asia-Pacific sources with publicly documented RSS feeds.
    ("联合早报（FeedX镜像）", "https://feedx.site/rss/zaobao.xml"),
    ("CNA 亚洲", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511"),
    ("CNA 新加坡", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416"),
    ("CNA 商业", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),
    ("南华早报中国", "https://www.scmp.com/rss/4/feed"),
    ("南华早报亚洲", "https://www.scmp.com/rss/35/feed"),
    ("The Japan Times", "https://www.japantimes.co.jp/feed/"),
)

FINANCIAL_SOURCES = (
    ("美联储新闻", "https://www.federalreserve.gov/feeds/press_all.xml", "宏观与央行", 1, "central_bank"),
    ("美联储货币政策", "https://www.federalreserve.gov/feeds/press_monetary.xml", "宏观与央行", 1, "central_bank"),
    ("欧洲央行新闻", "https://www.ecb.europa.eu/rss/press.html", "宏观与央行", 1, "central_bank"),
    ("国际清算银行", "https://www.bis.org/doclist/rss_all_categories.rss", "宏观与央行", 1, "institution"),
    ("国际清算银行统计", "https://www.bis.org/doclist/all_statistics.rss", "债券与外汇", 1, "institution"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "全球金融", 2, "media"),
    ("DW Business", "https://rss.dw.com/rdf/rss-en-bus", "全球金融", 2, "media"),
    ("CNA Business", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936", "亚洲市场", 2, "media"),
    ("英国央行新闻", "https://www.bankofengland.co.uk/rss/news", "宏观与央行", 1, "central_bank"),
    ("英国央行演讲", "https://www.bankofengland.co.uk/rss/speeches", "宏观与央行", 1, "central_bank"),
    ("日本央行更新", "https://www.boj.or.jp/en/rss/whatsnew.xml", "亚洲市场", 1, "central_bank"),
    ("日本央行统计", "https://www.boj.or.jp/en/rss/statistics.xml", "亚洲市场", 1, "central_bank"),
    ("韩国央行货币政策", "https://www.bok.or.kr/eng/bbs/E0000627/news.rss?menuNo=400022", "亚洲市场", 1, "central_bank"),
    ("韩国央行新闻", "https://www.bok.or.kr/eng/bbs/E0000634/news.rss?menuNo=400069", "亚洲市场", 1, "central_bank"),
    ("香港金管局新闻 API", "https://api.hkma.gov.hk/public/press-releases?lang=en", "亚洲市场", 1, "hkma_api"),
    ("港交所监管通讯", "https://www.hkex.com.hk/Services/RSS-Feeds/regulatory-announcements?sc_lang=en", "交易所公告", 1, "exchange"),
    ("Nasdaq Europe 主板公告", "https://api.news.eu.nasdaq.com/news/rss/mainMarketNotices", "交易所公告", 1, "exchange"),
    ("Nasdaq Europe First North 公告", "https://api.news.eu.nasdaq.com/news/rss/firstNorthNotices", "交易所公告", 1, "exchange"),
    ("国际货币基金组织新闻", "https://www.imf.org/en/News", "宏观与央行", 1, "imf_html"),
)

DEFAULT_TERMS = {
    "中国": "china,chinese,beijing,prc,taiwan,香港,hong kong",
    "科技": "technology,tech,ai,artificial intelligence,semiconductor,chip,cyber,digital",
    "政治": "politics,political,election,government,diplomacy,parliament,president",
    "经济": "economy,economic,market,trade,inflation,gdp,finance,tariff",
    "战争": "war,conflict,military,attack,ceasefire,missile,weapon,invasion",
}


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS news_sources (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
          enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
          column_name TEXT NOT NULL DEFAULT '全球要闻', source_tier INTEGER NOT NULL DEFAULT 2,
          source_kind TEXT NOT NULL DEFAULT 'media'
        );
        CREATE TABLE IF NOT EXISTS topic_terms (
          topic TEXT PRIMARY KEY, terms TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS news_items (
          fingerprint TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
          translated_title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
          translated_summary TEXT NOT NULL DEFAULT '', url TEXT NOT NULL, published_at TEXT NOT NULL,
          topics TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,
          column_name TEXT NOT NULL DEFAULT '全球要闻', entities TEXT NOT NULL DEFAULT '',
          event_type TEXT NOT NULL DEFAULT '', impact_score INTEGER NOT NULL DEFAULT 0,
          impact_level TEXT NOT NULL DEFAULT '低', impact_reason TEXT NOT NULL DEFAULT '',
          model_summary TEXT NOT NULL DEFAULT '', model_status TEXT NOT NULL DEFAULT 'rules'
        );
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL, collected_count INTEGER NOT NULL DEFAULT 0,
          new_count INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS digest_reports (
          token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          item_fingerprints TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sec_watchlist (
          cik TEXT PRIMARY KEY, company_name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          forms TEXT NOT NULL DEFAULT '8-K,10-K,10-Q,6-K,20-F', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_digest_reports_expires ON digest_reports(expires_at);
        """)
        _ensure_columns(db, "news_sources", {"column_name": "TEXT NOT NULL DEFAULT '全球要闻'", "source_tier": "INTEGER NOT NULL DEFAULT 2", "source_kind": "TEXT NOT NULL DEFAULT 'media'"})
        _ensure_columns(db, "news_items", {"column_name": "TEXT NOT NULL DEFAULT '全球要闻'", "entities": "TEXT NOT NULL DEFAULT ''", "event_type": "TEXT NOT NULL DEFAULT ''", "impact_score": "INTEGER NOT NULL DEFAULT 0", "impact_level": "TEXT NOT NULL DEFAULT '低'", "impact_reason": "TEXT NOT NULL DEFAULT ''", "model_summary": "TEXT NOT NULL DEFAULT ''", "model_status": "TEXT NOT NULL DEFAULT 'rules'"})
        for name, url in DEFAULT_SOURCES:
            db.execute("INSERT OR IGNORE INTO news_sources(name,url,created_at) VALUES(?,?,?)", (name, url, now_text()))
        for name, url, column, tier, kind in FINANCIAL_SOURCES:
            db.execute("""INSERT INTO news_sources(name,url,created_at,column_name,source_tier,source_kind) VALUES(?,?,?,?,?,?)
              ON CONFLICT(url) DO UPDATE SET column_name=excluded.column_name,source_tier=excluded.source_tier,source_kind=excluded.source_kind""",
                       (name, url, now_text(), column, tier, kind))
        for topic, terms in DEFAULT_TERMS.items():
            db.execute("INSERT OR IGNORE INTO topic_terms(topic,terms) VALUES(?,?)", (topic, terms))
        for key, value, secret in (("admin_username", os.getenv("ADMIN_USERNAME", "admin"), False), ("schedule", "08:00", False),
                                   ("wecom_webhook", os.getenv("WECOM_WEBHOOK", ""), True), ("translation_mode", "argos", False),
                                   ("wecom_corp_id", os.getenv("WECOM_CORP_ID", ""), True), ("wecom_agent_id", os.getenv("WECOM_AGENT_ID", ""), False),
                                   ("wecom_app_secret", os.getenv("WECOM_APP_SECRET", ""), True), ("wecom_callback_token", os.getenv("WECOM_CALLBACK_TOKEN", ""), True),
                                   ("wecom_encoding_aes_key", os.getenv("WECOM_ENCODING_AES_KEY", ""), True),
                                   ("wecom_admin_users", os.getenv("WECOM_ADMIN_USERS", ""), False),
                                   ("wecom_public_url", os.getenv("WECOM_PUBLIC_URL", ""), False),
                                   ("telegram_bot_token", os.getenv("TELEGRAM_BOT_TOKEN", ""), True),
                                   ("telegram_webhook_secret", os.getenv("TELEGRAM_WEBHOOK_SECRET", ""), True),
                                   ("telegram_admin_users", os.getenv("TELEGRAM_ADMIN_USERS", ""), False),
                                   ("telegram_public_url", os.getenv("TELEGRAM_PUBLIC_URL", ""), False)):
            existing = db.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone()
            if not existing:
                stored = "enc:" + _cipher().encrypt(value.encode()).decode() if secret and value else value
                db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, stored))
        for key, value in (("digest_public_url", os.getenv("DIGEST_PUBLIC_URL", "")),
                           ("digest_retention_days", "7"), ("digest_headline_count", "5")):
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        for key, value in (("finance_enabled", "1"), ("finance_min_score", "35"), ("local_model_enabled", "1"),
                           ("local_model_endpoint", "http://127.0.0.1:8082"), ("local_model_name", "qwen3-1.7b"),
                           ("model_min_score", "35"), ("model_max_score", "74"), ("model_max_items", "12")):
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('sec_user_agent',?)", (os.getenv("SEC_USER_AGENT", ""),))


def _ensure_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def create_digest_report(fingerprints: list[str], retention_days: int) -> str:
    """Create an unguessable, read-only snapshot and return its bearer token."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    created = datetime.now().astimezone()
    expires = created.timestamp() + max(1, min(retention_days, 30)) * 86400
    with connect() as db:
        db.execute("DELETE FROM digest_reports WHERE expires_at < ? OR revoked=1", (created.isoformat(timespec="seconds"),))
        db.execute("INSERT INTO digest_reports(token_hash,created_at,expires_at,item_fingerprints) VALUES(?,?,?,?)",
                   (token_hash, created.isoformat(timespec="seconds"),
                    datetime.fromtimestamp(expires, created.tzinfo).isoformat(timespec="seconds"),
                    json.dumps(list(dict.fromkeys(fingerprints)))))
    return token


def read_digest_report(token: str) -> tuple[dict | None, list[dict]]:
    if not token or len(token) > 200:
        return None, []
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as db:
        report = db.execute("SELECT * FROM digest_reports WHERE token_hash=? AND revoked=0 AND expires_at>=?", (token_hash, now)).fetchone()
        if not report:
            return None, []
        fingerprints = json.loads(report["item_fingerprints"])
        if not fingerprints:
            return dict(report), []
        placeholders = ",".join("?" for _ in fingerprints)
        rows = db.execute(f"SELECT * FROM news_items WHERE fingerprint IN ({placeholders}) ORDER BY priority DESC,first_seen_at DESC", fingerprints).fetchall()
    return dict(report), [dict(row) for row in rows]


def _cipher() -> Fernet:
    secret = os.getenv("APP_SECRET", "development-secret-change-me").encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def setting(key: str, default: str = "", secret: bool = False) -> str:
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    value = row["value"] if row else default
    if secret and value.startswith("enc:"):
        try: return _cipher().decrypt(value[4:].encode()).decode()
        except InvalidToken: return ""
    return value


def set_setting(key: str, value: str, secret: bool = False) -> None:
    if secret: value = "enc:" + _cipher().encrypt(value.encode()).decode()
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def backup_database(retention_days: int) -> Path:
    """Create a consistent SQLite backup and prune expired copies."""
    source_path = Path(db_path())
    target_dir = Path(os.getenv("BACKUP_DIR", str(source_path.parent / "backups")))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"news-{datetime.now().astimezone():%Y%m%d-%H%M%S}.sqlite3"
    source, destination = sqlite3.connect(source_path), sqlite3.connect(target)
    try: source.backup(destination)
    finally: destination.close(); source.close()
    cutoff = datetime.now().astimezone().timestamp() - max(1, int(retention_days)) * 86400
    for candidate in target_dir.glob("news-*.sqlite3"):
        if candidate != target and candidate.stat().st_mtime < cutoff: candidate.unlink(missing_ok=True)
    return target
