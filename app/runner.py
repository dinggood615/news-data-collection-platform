from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html import unescape
from urllib.request import Request, urlopen

import feedparser
from langdetect import DetectorFactory, LangDetectException, detect

from .database import connect, now_text, setting

DetectorFactory.seed = 0



def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


def _term_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_terms = value
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raw_terms = re.split(r"[,，\n;；]+", text)
        else:
            raw_terms = parsed if isinstance(parsed, list) else [text]
    return [str(term).strip().casefold() for term in raw_terms if str(term).strip()]


def _topics(title: str, summary: str, rules: list[dict]) -> list[str]:
    text = f"{title} {summary}".casefold()
    matched = []
    for rule in rules:
        terms = _term_list(rule["terms"])
        if any(term and term in text for term in terms): matched.append(rule["topic"])
    return matched


def _local_translate(text: str) -> str:
    """Try the local LibreTranslate-compatible API first."""
    endpoint = setting("translation_endpoint", "http://127.0.0.1:5000/translate")
    body = json.dumps({"q": text[:2500], "source": "auto", "target": "zh", "format": "text"}).encode()
    request = Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=20) as response:
        return str(json.loads(response.read().decode())["translatedText"]).strip()


def _argos_translate(text: str) -> str:
    """Offline fallback using installed Argos language packages."""
    try:
        import argostranslate.translate
        try:
            source = detect(text[:1000]).lower().split("-")[0]
        except LangDetectException:
            source = "en"
        if source == "zh":
            return text
        languages = {language.code: language for language in argostranslate.translate.get_installed_languages()}
        if source not in languages or "zh" not in languages:
            return ""
        return languages[source].get_translation(languages["zh"]).translate(text[:2500]).strip()
    except Exception:
        return ""


def translate(text: str) -> str:
    """Free local translation with an offline Argos fallback; retain source on failure."""
    if not text or setting("translation_mode", "argos") == "off": return text
    try:
        translated = _local_translate(text)
    except Exception:
        translated = _argos_translate(text)
    return translated or text


def _wecom_markdown(items: list[dict]) -> str:
    sections = [f"# 国际新闻快报 · {datetime.now().astimezone():%Y-%m-%d %H:%M}"]
    for item in items:
        topics = " · ".join(item["topics"].split(","))
        title = item["translated_title"] or item["title"]
        summary = (item["translated_summary"] or item["summary"]).replace("\n", " ")[:180]
        sections.append(f"**【{topics}】**\n**{title[:100]}**\n> {summary}\n来源：{item['source']} · [阅读原文]({item['url']})")
    return "\n\n---\n\n".join(sections)


def send_wecom(items: list[dict]) -> str:
    webhook = setting("wecom_webhook", secret=True)
    if not webhook or not items: return "企业微信未配置或没有新增新闻"
    # WeCom markdown payloads have a practical size limit; split conservatively.
    chunks, current = [], []
    for item in items:
        candidate = _wecom_markdown(current + [item])
        if len(candidate.encode()) > 3500 and current:
            chunks.append(current); current = [item]
        else: current.append(item)
    if current: chunks.append(current)
    for chunk in chunks:
        body = json.dumps({"msgtype": "markdown", "markdown": {"content": _wecom_markdown(chunk)}}).encode()
        request = Request(webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=20) as response:
            if json.loads(response.read().decode()).get("errcode") != 0: raise RuntimeError("企业微信机器人拒绝消息")
    return f"企业微信已推送 {len(items)} 条新闻"


def send_wecom_test() -> str:
    """Send one non-persistent sample card to validate the configured webhook."""
    sample = {
        "source": "新闻数据采集平台", "title": "WeCom notification test",
        "translated_title": "企业微信推送测试成功", "summary": "This is a test message.",
        "translated_summary": "这是一条测试消息。企业微信机器人已成功接收新闻平台推送。",
        "url": "https://github.com/dinggood615/news-data-collection-platform",
        "topics": "科技,经济",
    }
    return send_wecom([sample])


def collect_news() -> tuple[int, int, str]:
    with connect() as db:
        sources = [dict(row) for row in db.execute("SELECT * FROM news_sources WHERE enabled=1")]
        rules = [dict(row) for row in db.execute("SELECT * FROM topic_terms WHERE enabled=1")]
    collected, warnings, accepted = 0, [], []
    for source in sources:
        try:
            parsed = feedparser.parse(source["url"], agent="NewsCollectionPlatform/1.0 (+public RSS)")
            if parsed.bozo and not parsed.entries: raise RuntimeError("RSS 无法解析")
            for entry in parsed.entries[:60]:
                title, summary, url = _plain(entry.get("title", "")), _plain(entry.get("summary", "")), entry.get("link", "")
                if not title or not url: continue
                topics = _topics(title, summary, rules)
                if not topics: continue
                collected += 1
                accepted.append({"source": source["name"], "title": title, "summary": summary, "url": url,
                                 "published_at": entry.get("published", entry.get("updated", "")), "topics": topics})
        except Exception as exc: warnings.append(f"{source['name']}：{type(exc).__name__}")
    new_items = []
    with connect() as db:
        for item in accepted:
            fingerprint = hashlib.sha256(item["url"].encode()).hexdigest()
            translated_title, translated_summary = translate(item["title"]), translate(item["summary"])
            cursor = db.execute("""INSERT OR IGNORE INTO news_items(fingerprint,source,title,translated_title,summary,translated_summary,url,published_at,topics,priority,first_seen_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (fingerprint, item["source"], item["title"], translated_title, item["summary"], translated_summary, item["url"], item["published_at"], ",".join(item["topics"]), len(item["topics"]), now_text()))
            if cursor.rowcount:
                item.update(translated_title=translated_title, translated_summary=translated_summary); new_items.append(item)
    message = send_wecom(new_items)
    return collected, len(new_items), "; ".join(warnings + [message])
