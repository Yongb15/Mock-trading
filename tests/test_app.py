"""앱 시작 모듈: 실행 파일이 이 모듈을 불러오므로, 문법 오류 하나로 실행 파일 전체가 안 뜨는 일을 막는다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402


def test_app_module_imports():
    import mock_trading.app  # noqa: F401


class FakeProvider:
    def get_quote(self, symbol):
        from mock_trading.domain import Currency, Quote

        return Quote(symbol, symbol, Decimal("100"), Currency.USD)


class OfflineProvider:
    def get_quote(self, symbol):
        raise ConnectionError("no network")


@pytest.fixture
def report(tmp_path):
    return tmp_path / "report.txt"


def test_self_check_builds_the_window_and_reports(report, monkeypatch):
    """Mock_QA K1: QApplication·메인 창·차트를 실제로 만들어 Qt 플러그인까지 확인한다."""
    from mock_trading import app, market_data

    monkeypatch.setattr(market_data, "YFinanceProvider", FakeProvider)
    assert app.self_check(str(report)) == 0
    text = report.read_text(encoding="utf-8")
    assert "db ok" in text and "gui ok platform=" in text and "quote ok AAPL 100" in text


def test_offline_is_skipped_not_failed(report, monkeypatch):
    """Mock_QA K2: 네트워크만 없으면 묶음은 정상(0)이고 SKIPPED로 적는다."""
    from mock_trading import app, market_data

    monkeypatch.setattr(market_data, "YFinanceProvider", OfflineProvider)
    assert app.self_check(str(report)) == 0
    assert "SKIPPED(network) ConnectionError: no network" in report.read_text(encoding="utf-8")


def test_broken_gui_is_a_failure(report, monkeypatch):
    from mock_trading import app, market_data

    monkeypatch.setattr(market_data, "YFinanceProvider", FakeProvider)

    def broken(*args, **kwargs):
        raise RuntimeError("could not load the Qt platform plugin")

    monkeypatch.setattr(app, "MainWindow", broken)
    assert app.self_check(str(report)) == 1
    assert "FAILED RuntimeError: could not load the Qt platform plugin" in report.read_text(encoding="utf-8")


def test_unwritable_report_has_its_own_exit_code(tmp_path, monkeypatch):
    """Mock_QA K3: 결과 파일을 못 쓰면 조용히 끝나지 않고 종료 코드 2."""
    from mock_trading import app, market_data

    monkeypatch.setattr(market_data, "YFinanceProvider", FakeProvider)
    assert app.self_check(str(tmp_path / "없는 폴더" / "report.txt")) == 2


def test_db_path_override_is_made_absolute(tmp_path, monkeypatch):
    """Mock_Sequrity Info: 상대 경로면 실행 폴더마다 다른 DB가 열리므로 절대 경로로."""
    from mock_trading.storage import default_db_path

    monkeypatch.setenv("MOCK_TRADING_DB", str(tmp_path / "x.db"))
    assert default_db_path() == (tmp_path / "x.db").resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MOCK_TRADING_DB", "relative.db")
    assert default_db_path().is_absolute() and default_db_path() == (tmp_path / "relative.db").resolve()
    monkeypatch.delenv("MOCK_TRADING_DB")
    assert default_db_path().name == "mock_trading.db"
