from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html import unescape
from urllib.request import Request, urlopen

import feedparser
from langdetect import DetectorFactory, LangDetectException, detect

from .database import connect, create_digest_report, now_text, setting
from .intelligence import classify, enrich_with_local_model, local_model_session
from .connectors.official import collect_hkma, collect_imf, collect_sec

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


def _topic_text(value) -> str:
    if isinstance(value, (list, tuple, set)):
        return " · ".join(str(topic) for topic in value if str(topic).strip())
    return " · ".join(str(value or "").split(","))


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


def _wecom_markdown(items: list[dict], report_url: str) -> str:
    topic_counts: dict[str, int] = {}
    for item in items:
        topics_value = item.get("topics", "")
        topics_list = topics_value if isinstance(topics_value, (list, tuple, set)) else str(topics_value).split(",")
        for topic in topics_list:
            if topic.strip():
                topic_counts[topic.strip()] = topic_counts.get(topic.strip(), 0) + 1
    count_text = " · ".join(f"{topic}{count}" for topic, count in sorted(topic_counts.items(), key=lambda pair: pair[1], reverse=True))
    column_counts: dict[str, int] = {}
    for item in items:
        column = item.get("column_name", "全球要闻")
        column_counts[column] = column_counts.get(column, 0) + 1
    columns = " · ".join(f"{name}{count}" for name, count in sorted(column_counts.items(), key=lambda pair: pair[1], reverse=True))
    sections = [f"# 全球金融情报日报 · {datetime.now().astimezone():%Y-%m-%d %H:%M}", f"> 本次新增 **{len(items)}** 条  {columns or count_text}"]
    headline_count = max(1, min(int(setting("digest_headline_count", "5")), 10))
    for index, item in enumerate(sorted(items, key=lambda row: row.get("priority", 0), reverse=True)[:headline_count], 1):
        topics = _topic_text(item["topics"])
        title = item["translated_title"] or item["title"]
        sections.append(f"**{index}. 【{item.get('column_name') or topics}｜影响{item.get('impact_level', '待评估')}】{title[:90]}**\n> {item['source']}")
    sections.append(f"[查看全部 {len(items)} 条分栏资讯（免登录）]({report_url})\n> AI 摘要仅供研究，不构成投资建议；请核验原始来源。")
    return "\n\n".join(sections)


def send_wecom(items: list[dict]) -> str:
    webhook = setting("wecom_webhook", secret=True)
    if not webhook or not items: return "企业微信未配置或没有新增新闻"
    public_url = (setting("digest_public_url") or setting("wecom_public_url") or setting("telegram_public_url")).rstrip("/")
    if not public_url.startswith("https://"):
        return "企业微信未推送：请先填写日报 HTTPS 公网地址"
    retention = max(1, min(int(setting("digest_retention_days", "7")), 30))
    token = create_digest_report([item["fingerprint"] for item in items], retention)
    report_url = f"{public_url}/digest/{token}"
    content = _wecom_markdown(items, report_url)
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}, ensure_ascii=False).encode()
    request = Request(webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=20) as response:
        if json.loads(response.read().decode()).get("errcode") != 0: raise RuntimeError("企业微信机器人拒绝消息")
    return f"企业微信已发送 1 条汇总，包含 {len(items)} 条新闻"


def send_wecom_test() -> str:
    """Send one non-persistent sample card to validate the configured webhook."""
    sample = {
        "fingerprint": hashlib.sha256(b"wecom-test-digest").hexdigest(),
        "source": "新闻数据采集平台", "title": "WeCom notification test",
        "translated_title": "企业微信推送测试成功", "summary": "This is a test message.",
        "translated_summary": "这是一条测试消息。企业微信机器人已成功接收新闻平台推送。",
        "url": "https://github.com/dinggood615/news-data-collection-platform",
        "topics": "科技,经济",
    }
    with connect() as db:
        db.execute("""INSERT OR IGNORE INTO news_items(fingerprint,source,title,translated_title,summary,translated_summary,url,published_at,topics,priority,first_seen_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (sample["fingerprint"], sample["source"], sample["title"], sample["translated_title"], sample["summary"], sample["translated_summary"], sample["url"], now_text(), sample["topics"], 2, now_text()))
    return send_wecom([sample])


def collect_news() -> tuple[int, int, str]:
    with connect() as db:
        sources = [dict(row) for row in db.execute("SELECT * FROM news_sources WHERE enabled=1")]
        rules = [dict(row) for row in db.execute("SELECT * FROM topic_terms WHERE enabled=1")]
        sec_watchlist = [dict(row) for row in db.execute("SELECT * FROM sec_watchlist WHERE enabled=1")]
    collected, warnings, accepted = 0, [], []
    for source in sources:
        try:
            if source.get("source_kind") == "hkma_api":
                entries = collect_hkma(source["url"])
            elif source.get("source_kind") == "imf_html":
                entries = collect_imf(source["url"])
            else:
                parsed = feedparser.parse(source["url"], agent="NewsCollectionPlatform/1.0 (+public RSS)")
                if parsed.bozo and not parsed.entries: raise RuntimeError("RSS 无法解析")
                entries = parsed.entries
            for entry in entries[:60]:
                title, summary, url = _plain(entry.get("title", "")), _plain(entry.get("summary", "")), entry.get("link", "")
                if not title or not url: continue
                topics = _topics(title, summary, rules)
                intel = classify(title, summary, int(source.get("source_tier", 2)), source.get("column_name", "全球要闻"))
                if not topics and intel["impact_score"] < int(setting("finance_min_score", "35")): continue
                collected += 1
                accepted.append({"source": source["name"], "title": title, "summary": summary, "url": url,
                                 "published_at": entry.get("published", entry.get("updated", "")), "topics": topics or [intel["column_name"]], **intel})
        except Exception as exc: warnings.append(f"{source['name']}：{type(exc).__name__}")
    sec_items, sec_warnings = collect_sec(sec_watchlist, setting("sec_user_agent", ""))
    warnings.extend(sec_warnings)
    for item in sec_items:
        intel = classify(item["title"], item["summary"], 1, item["column_name"])
        accepted.append({**item, **intel, "column_name": item["column_name"]})
        collected += 1
    candidates = sorted((item for item in accepted if int(setting("model_min_score", "35")) <= item["impact_score"] <= int(setting("model_max_score", "74"))), key=lambda row: row["impact_score"], reverse=True)[:max(0, min(int(setting("model_max_items", "12")), 30))]
    if setting("local_model_enabled", "1") == "1" and candidates:
        endpoint = setting("local_model_endpoint", "http://127.0.0.1:8083")
        with local_model_session(endpoint):
            for item in candidates:
                try: enrich_with_local_model(item, endpoint, setting("local_model_name", "qwen3-1.7b"))
                except Exception: item["model_status"] = "fallback"
    accepted = [item for item in accepted if item.get("model_status") != "rejected"]
    new_items = []
    with connect() as db:
        for item in accepted:
            fingerprint = hashlib.sha256(item["url"].encode()).hexdigest()
            cursor = db.execute("""INSERT OR IGNORE INTO news_items(fingerprint,source,title,translated_title,summary,translated_summary,url,published_at,topics,priority,first_seen_at,column_name,entities,event_type,impact_score,impact_level,impact_reason,model_summary,model_status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (fingerprint, item["source"], item["title"], "", item["summary"], "", item["url"], item["published_at"], ",".join(item["topics"]), item["impact_score"], now_text(), item["column_name"], item["entities"], item["event_type"], item["impact_score"], item["impact_level"], item["impact_reason"], item["model_summary"], item["model_status"]))
            if cursor.rowcount:
                translated_title, translated_summary = translate(item["title"]), translate(item["summary"])
                final_summary = item["model_summary"] or translated_summary
                db.execute("""UPDATE news_items SET translated_title=?,translated_summary=? WHERE fingerprint=?""", (translated_title, final_summary, fingerprint))
                item.update(fingerprint=fingerprint, priority=item["impact_score"], translated_title=translated_title, translated_summary=final_summary); new_items.append(item)
    message = send_wecom(new_items)
    return collected, len(new_items), "; ".join(warnings + [message])
