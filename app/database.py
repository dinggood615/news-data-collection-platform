from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
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
          enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS topic_terms (
          topic TEXT PRIMARY KEY, terms TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS news_items (
          fingerprint TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
          translated_title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
          translated_summary TEXT NOT NULL DEFAULT '', url TEXT NOT NULL, published_at TEXT NOT NULL,
          topics TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL, collected_count INTEGER NOT NULL DEFAULT 0,
          new_count INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT ''
        );
        """)
        for name, url in DEFAULT_SOURCES:
            db.execute("INSERT OR IGNORE INTO news_sources(name,url,created_at) VALUES(?,?,?)", (name, url, now_text()))
        for topic, terms in DEFAULT_TERMS.items():
            db.execute("INSERT OR IGNORE INTO topic_terms(topic,terms) VALUES(?,?)", (topic, terms))
        for key, value in (("admin_username", os.getenv("ADMIN_USERNAME", "admin")), ("schedule", "08:00"),
                           ("wecom_webhook", os.getenv("WECOM_WEBHOOK", "")), ("translation_mode", "argos")):
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))


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
