from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.database import connect, create_digest_report, init_db, set_setting
from app.main import app
from app.runner import send_wecom


def _insert_news(fingerprint: str, title: str = "科技新闻") -> dict:
    with connect() as db:
        db.execute("""INSERT INTO news_items(fingerprint,source,title,translated_title,summary,translated_summary,url,published_at,topics,priority,first_seen_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (fingerprint, "测试媒体", title, title, "摘要", "中文摘要", f"https://example.com/{fingerprint}", "", "科技,中国", 2, datetime.now().astimezone().isoformat()))
    return {"fingerprint": fingerprint, "source": "测试媒体", "title": title, "translated_title": title,
            "summary": "摘要", "translated_summary": "中文摘要", "url": f"https://example.com/{fingerprint}",
            "topics": ["科技", "中国"], "priority": 2}


def test_digest_is_public_read_only_and_unknown_token_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "news.sqlite3"))
    init_db()
    item = _insert_news("fp1")
    token = create_digest_report([item["fingerprint"]], 7)
    with TestClient(app) as client:
        response = client.get(f"/digest/{token}")
        assert response.status_code == 200
        assert "科技新闻" in response.text
        assert client.get("/digest/not-a-valid-token").status_code == 404
        assert client.get("/").status_code == 401


def test_wecom_sends_exactly_one_request_for_many_items(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "news.sqlite3"))
    init_db()
    set_setting("wecom_webhook", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test", secret=True)
    set_setting("digest_public_url", "https://news.example.com")
    items = [_insert_news(f"fp{index}", f"新闻 {index}") for index in range(30)]
    response = type("Response", (), {"read": lambda self: b'{"errcode":0}', "__enter__": lambda self: self, "__exit__": lambda *args: None})()
    with patch("app.runner.urlopen", return_value=response) as opener:
        message = send_wecom(items)
    assert opener.call_count == 1
    assert "1 条汇总" in message
    request_body = opener.call_args.args[0].data.decode("utf-8")
    assert "/digest/" in request_body
    assert "30" in request_body
