import json
from unittest.mock import patch

from app.connectors.official import collect_hkma, collect_sec


def _response(payload):
    body = json.dumps(payload).encode()
    return type("Response", (), {"read": lambda self: body, "__enter__": lambda self: self, "__exit__": lambda *args: None})()


def test_sec_requires_identifying_user_agent():
    items, warnings = collect_sec([{"cik": "320193", "company_name": "Apple", "forms": "8-K"}], "anonymous")
    assert not items
    assert "User-Agent" in warnings[0]


def test_sec_watchlist_filters_forms_and_builds_archive_url():
    payload = {"name": "Example Corp", "filings": {"recent": {"form": ["8-K", "4"], "accessionNumber": ["0000000001-26-000001", "x"],
               "primaryDocument": ["report.htm", "ownership.xml"], "filingDate": ["2026-08-11", "2026-08-11"], "primaryDocDescription": ["Current report", "Ownership"]}}}
    with patch("app.connectors.official.urlopen", return_value=_response(payload)), patch("app.connectors.official.time.sleep"):
        items, warnings = collect_sec([{"cik": "1", "company_name": "Example", "forms": "8-K"}], "Platform admin@example.com")
    assert not warnings and len(items) == 1
    assert items[0]["url"].endswith("/000000000126000001/report.htm")
    assert items[0]["column_name"] == "SEC公司观察"


def test_hkma_open_api_records_are_normalized():
    payload = {"result": {"records": [{"title": "Base rate decision", "link": "https://example.hk/item", "date": "2026-08-11"}]}}
    with patch("app.connectors.official.urlopen", return_value=_response(payload)):
        items = collect_hkma("https://api.hkma.gov.hk/public/press-releases?lang=en")
    assert items[0]["title"] == "Base rate decision"
