from __future__ import annotations

import html
import json
import re
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen


SEC_FORMS = {"8-K", "10-K", "10-Q", "6-K", "20-F", "40-F", "S-1", "F-1", "DEF 14A"}


def _json(url: str, user_agent: str) -> dict:
    # Let urllib negotiate an uncompressed response. Some official APIs return
    # gzip when explicitly requested, while urllib does not auto-decompress it.
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def collect_sec(watchlist: list[dict], user_agent: str) -> tuple[list[dict], list[str]]:
    if not watchlist:
        return [], []
    if "@" not in user_agent:
        return [], ["SEC观察名单：请先填写含联系邮箱的 SEC User-Agent"]
    items, warnings = [], []
    for company in watchlist[:50]:
        try:
            cik = re.sub(r"\D", "", company["cik"]).zfill(10)
            data = _json(f"https://data.sec.gov/submissions/CIK{cik}.json", user_agent)
            recent = data.get("filings", {}).get("recent", {})
            allowed = {part.strip().upper() for part in company["forms"].split(",") if part.strip()} or SEC_FORMS
            for index, form in enumerate(recent.get("form", [])[:100]):
                if form.upper() not in allowed:
                    continue
                accession = recent["accessionNumber"][index]
                accession_flat = accession.replace("-", "")
                primary = recent["primaryDocument"][index]
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_flat}/{primary}"
                filed = recent.get("filingDate", [""] * 100)[index]
                description = recent.get("primaryDocDescription", [""] * 100)[index]
                items.append({"source": f"SEC · {data.get('name') or company['company_name']}", "title": f"{form} · {description or data.get('name')}",
                              "summary": f"SEC EDGAR filing {accession}; filed {filed}.", "url": url, "published_at": filed,
                              "topics": ["监管与披露"], "column_name": "SEC公司观察", "source_tier": 1})
            time.sleep(0.12)
        except Exception as exc:
            warnings.append(f"SEC {company['company_name']}：{type(exc).__name__}")
    return items, warnings


def collect_hkma(url: str) -> list[dict]:
    data = _json(url + "&offset=0", "NewsCollectionPlatform/1.0")
    rows = data.get("result", {}).get("records", data.get("result", {}).get("datas", []))
    return [{"title": row.get("title", ""), "summary": "", "url": row.get("link", ""), "published_at": row.get("date", "")}
            for row in rows[:60] if row.get("title") and row.get("link")]


def collect_imf(url: str) -> list[dict]:
    request = Request(url, headers={"User-Agent": "NewsCollectionPlatform/1.0 (+public news index)", "Accept-Language": "en"})
    with urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", "ignore")
    links = re.findall(r'href=["\']([^"\']*/en/News/Articles/[^"\']+)["\'][^>]*>(.*?)</a>', body, re.I | re.S)
    result, seen = [], set()
    for href, label in links:
        title = html.unescape(re.sub(r"<[^>]+>", " ", label))
        title = re.sub(r"\s+", " ", title).strip()
        full_url = urljoin(url, href)
        if title and full_url not in seen:
            seen.add(full_url); result.append({"title": title, "summary": "", "url": full_url, "published_at": ""})
    return result[:60]
