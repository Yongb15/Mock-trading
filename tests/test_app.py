"""앱 시작 모듈: 실행 파일이 이 모듈을 불러오므로, 문법 오류 하나로 실행 파일 전체가 안 뜨는 일을 막는다."""
from decimal import Decimal


def test_app_module_imports():
    import mock_trading.app  # noqa: F401


def test_self_check_writes_report(tmp_path, monkeypatch):
    from mock_trading import app, market_data
    from mock_trading.domain import Currency, Quote

    class Fake:
        def get_quote(self, symbol):
            return Quote(symbol, symbol, Decimal("100"), Currency.USD)

    monkeypatch.setattr(market_data, "YFinanceProvider", Fake)
    report = tmp_path / "report.txt"
    assert app.self_check(str(report)) == 0
    text = report.read_text(encoding="utf-8")
    assert "db ok" in text and "quote ok AAPL 100" in text


def test_self_check_reports_failure(tmp_path, monkeypatch):
    from mock_trading import app, market_data

    class Broken:
        def get_quote(self, symbol):
            raise RuntimeError("network down")

    monkeypatch.setattr(market_data, "YFinanceProvider", Broken)
    report = tmp_path / "report.txt"
    assert app.self_check(str(report)) == 1
    assert "FAILED RuntimeError: network down" in report.read_text(encoding="utf-8")


def test_db_path_can_be_overridden(tmp_path, monkeypatch):
    from mock_trading.storage import default_db_path

    monkeypatch.setenv("MOCK_TRADING_DB", str(tmp_path / "x.db"))
    assert default_db_path() == tmp_path / "x.db"
    monkeypatch.delenv("MOCK_TRADING_DB")
    assert default_db_path().name == "mock_trading.db"
