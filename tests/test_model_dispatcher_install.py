import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_news_installer_contains_standalone_dispatcher():
    dispatcher = (ROOT / "scripts" / "local-model-dispatcher.py").read_text(encoding="utf-8")
    ast.parse(dispatcher)
    model_installer = (ROOT / "scripts" / "install-local-model.sh").read_text(encoding="utf-8")
    main_installer = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
    assert "local-model-dispatcher.service" in model_installer
    assert "scripts/install-local-model.sh" in main_installer
    assert "127.0.0.1" in dispatcher


def test_news_defaults_and_migrates_to_shared_dispatcher():
    runner = (ROOT / "app" / "runner.py").read_text(encoding="utf-8")
    database = (ROOT / "app" / "database.py").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8083" in runner
    assert "http://127.0.0.1:8083" in database
    assert "UPDATE settings SET value='http://127.0.0.1:8083'" in database
