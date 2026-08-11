from __future__ import annotations

import json
import re
import os
import subprocess
import time
from contextlib import contextmanager
from urllib.request import Request, urlopen


FINANCE_RULES = {
    "宏观与央行": ("interest rate", "rate cut", "rate hike", "inflation", "cpi", "gdp", "payroll", "monetary policy", "央行", "降息", "加息", "通胀"),
    "股票与公司": ("earnings", "guidance", "profit warning", "merger", "acquisition", "buyback", "dividend", "ipo", "bankruptcy", "财报", "并购", "回购"),
    "债券与外汇": ("treasury", "bond", "yield", "credit spread", "forex", "dollar", "yuan", "euro", "yen", "国债", "收益率", "汇率"),
    "商品与能源": ("oil", "brent", "gas", "gold", "copper", "opec", "commodity", "原油", "黄金", "铜", "能源"),
    "监管与披露": ("sec filing", "10-k", "10-q", "8-k", "enforcement", "antitrust", "regulator", "监管", "处罚", "披露"),
    "金融风险": ("default", "downgrade", "liquidity", "bank run", "sanction", "cyberattack", "违约", "降级", "流动性", "制裁"),
}

HIGH_IMPACT = ("rate cut", "rate hike", "emergency", "default", "bankruptcy", "war", "sanction", "merger", "acquisition", "profit warning", "降息", "加息", "违约", "破产")
LOW_SIGNAL = ("opinion", "podcast", "week ahead", "what to watch", "观点", "展望")


def _contains(text: str, phrase: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", phrase):
        return phrase in text
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def classify(title: str, summary: str, source_tier: int = 2, source_column: str = "全球要闻") -> dict:
    title_text, full_text = title.casefold(), f"{title} {summary}".casefold()
    matches = {column: [term for term in terms if _contains(full_text, term)] for column, terms in FINANCE_RULES.items()}
    matches = {column: terms for column, terms in matches.items() if terms}
    column = max(matches, key=lambda key: len(matches[key]) + 2 * sum(_contains(title_text, term) for term in matches[key]), default=source_column)
    term_count = sum(len(values) for values in matches.values())
    title_hits = sum(_contains(title_text, term) for values in matches.values() for term in values)
    score = max(0, min(100, 12 + (4 - max(1, min(source_tier, 3))) * 9 + term_count * 8 + title_hits * 8))
    if any(_contains(full_text, term) for term in HIGH_IMPACT): score = min(100, score + 18)
    if any(_contains(title_text, term) for term in LOW_SIGNAL): score = max(0, score - 18)
    level = "高" if score >= 75 else "中" if score >= 45 else "低"
    reason = f"命中{term_count}个金融事件词；来源等级T{source_tier}"
    return {"column_name": column, "impact_score": score, "impact_level": level,
            "event_type": column, "entities": "", "impact_reason": reason, "model_summary": "", "model_status": "rules"}


def enrich_with_local_model(item: dict, endpoint: str, model: str, timeout: int = 45) -> dict:
    prompt = """你是金融新闻编辑。仅依据输入，不补充未知事实。输出严格JSON：relevant(boolean), impact_score(0-100), event_type, entities(字符串数组), reason(中文不超过60字), zh_summary(中文不超过120字), risk_note(中文不超过40字)。这不是投资建议。\n""" + json.dumps({"title": item["title"], "summary": item.get("summary", ""), "source": item["source"]}, ensure_ascii=False)
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 350}
    request = Request(endpoint.rstrip("/") + "/v1/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        content = json.loads(response.read().decode())["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", content, re.S)
    if not match: raise ValueError("model did not return JSON")
    result = json.loads(match.group(0))
    item.update(impact_score=max(0, min(100, int(result.get("impact_score", item["impact_score"])))),
                impact_level="高" if int(result.get("impact_score", 0)) >= 75 else "中" if int(result.get("impact_score", 0)) >= 45 else "低",
                event_type=str(result.get("event_type") or item["event_type"])[:40],
                entities=",".join(str(x) for x in result.get("entities", [])[:10]),
                impact_reason=str(result.get("reason", ""))[:120], model_summary=str(result.get("zh_summary", ""))[:300],
                model_status="accepted" if result.get("relevant", True) else "rejected")
    return item


@contextmanager
def local_model_session(endpoint: str):
    """Start the small local GGUF server only for this collection run when needed."""
    health = endpoint.rstrip("/") + "/health"
    try:
        urlopen(health, timeout=2)
        yield
        return
    except Exception:
        pass
    binary = os.getenv("LLAMA_SERVER", "/opt/llama.cpp/llama-server")
    model = os.getenv("FINANCE_MODEL_PATH", "/opt/local-llm/models/qwen3-1.7b-q4_k_m.gguf")
    if not (os.path.isfile(binary) and os.path.isfile(model)):
        yield
        return
    process = subprocess.Popen([binary, "-m", model, "--host", "127.0.0.1", "--port", "8082", "--ctx-size", "2048",
                                "--threads", "3", "--parallel", "1", "--jinja", "--reasoning", "off", "--no-webui"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            if process.poll() is not None: break
            try:
                urlopen(health, timeout=1)
                break
            except Exception:
                time.sleep(1)
        yield
    finally:
        process.terminate()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill()
