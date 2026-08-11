import json
from unittest.mock import patch

from app.intelligence import classify, enrich_with_local_model


def test_phrase_matching_avoids_partial_english_words():
    weak = classify("Dollarization research podcast", "No market event", 3)
    strong = classify("Federal Reserve announces emergency rate cut", "Bond yields and the dollar fell", 1)
    assert strong["column_name"] == "宏观与央行"
    assert strong["impact_score"] >= 75
    assert strong["impact_score"] > weak["impact_score"]


def test_local_model_strict_json_is_applied():
    result = {"relevant": True, "impact_score": 82, "event_type": "央行决议", "entities": ["Federal Reserve"],
              "reason": "利率变化可能重定价全球资产", "zh_summary": "美联储调整利率。", "risk_note": "关注后续数据"}
    response_body = json.dumps({"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]}).encode()
    response = type("Response", (), {"read": lambda self: response_body, "__enter__": lambda self: self, "__exit__": lambda *args: None})()
    item = {"title": "Fed changes rates", "summary": "", "source": "Federal Reserve", **classify("Fed changes rates", "", 1)}
    with patch("app.intelligence.urlopen", return_value=response):
        enriched = enrich_with_local_model(item, "http://127.0.0.1:8082", "qwen")
    assert enriched["impact_score"] == 82
    assert enriched["model_status"] == "accepted"
    assert "Federal Reserve" in enriched["entities"]
