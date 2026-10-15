import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from mock_trading.domain import Currency, Quote, TradingError  # noqa: E402
from mock_trading.storage import Repository  # noqa: E402
from mock_trading.watchlist import MAX_WATCH_ITEMS, Watchlist  # noqa: E402

from .test_broker import FakeProvider  # noqa: E402


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def watchlist(provider):
    return Watchlist(Repository(), provider)


def test_add_stores_resolved_symbol_and_name(watchlist):
    quote = watchlist.add("005930")
    assert quote.symbol == "005930.KS"
    [row] = watchlist.rows()
    assert (row.item.symbol, row.item.name, row.item.currency) == ("005930.KS", "Samsung", Currency.KRW)
    assert row.quote.price == Decimal("70000")
    assert watchlist.contains("005930.KS")


def test_adding_twice_keeps_one_entry(watchlist):
    watchlist.add("AAPL")
    watchlist.add("aapl")
    assert [r.item.symbol for r in watchlist.rows()] == ["AAPL"]


def test_unknown_or_garbage_symbol_is_not_stored(watchlist):
    with pytest.raises(TradingError):
        watchlist.add("NOPE")
    with pytest.raises(TradingError):
        watchlist.add("AAPL; DROP TABLE watchlist")
    assert watchlist.rows() == []


def test_remove(watchlist):
    watchlist.add("AAPL")
    assert watchlist.remove("AAPL") is True
    assert watchlist.remove("AAPL") is False
    assert watchlist.rows() == []


def test_limit(watchlist, provider):
    for i in range(MAX_WATCH_ITEMS):
        symbol = f"T{i}"
        provider.quotes[symbol] = Quote(symbol, symbol, Decimal("10"), Currency.USD, Decimal("10"))
        watchlist.add(symbol)
    with pytest.raises(TradingError, match=f"{MAX_WATCH_ITEMS}개"):
        watchlist.add("AAPL")
    watchlist.add("T0")  # 이미 있는 종목은 한도와 상관없이 그대로
    assert len(watchlist.rows()) == MAX_WATCH_ITEMS


def test_failing_quote_shows_row_without_price(watchlist, provider):
    watchlist.add("AAPL")
    watchlist.add("005930")
    del provider.quotes["AAPL"]
    rows = {r.item.symbol: r for r in watchlist.rows()}
    assert rows["AAPL"].quote is None
    assert rows["005930.KS"].quote is not None


def test_watchlist_survives_account_reset(provider):
    repo = Repository()
    watchlist = Watchlist(repo, provider)
    repo.reset_account(Decimal("1000000"))
    watchlist.add("AAPL")
    repo.reset_account(Decimal("2000000"))
    assert [r.item.symbol for r in watchlist.rows()] == ["AAPL"]


def test_watch_toggle_in_window(provider):
    from PySide6.QtWidgets import QApplication

    from mock_trading.broker import Broker
    from mock_trading.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    broker = Broker(Repository(check_same_thread=False), provider)
    broker.open_account(10_000_000)
    window = MainWindow(broker)

    def settle():
        window.worker.wait(3000)
        deadline = time.time() + 1
        while time.time() < deadline:
            app.processEvents()

    window.load_quote("AAPL")
    settle()
    assert window.watch_button.text() == "☆ 관심"
    window.toggle_watch()
    settle()
    assert window.watch_button.text() == "★ 관심"
    assert window.watch_table.rowCount() == 1
    assert window.watch_table.item(0, 1).text() == "AAPL"
    window.toggle_watch()
    settle()
    assert window.watch_button.text() == "☆ 관심"
    assert window.watch_table.rowCount() == 0
    window.close()


# ---------------------------------------------------------------- 리뷰(f795e18) 반영
import sqlite3  # noqa: E402

from mock_trading.broker import Broker  # noqa: E402


def test_remove_and_contains_accept_code_without_market_suffix(watchlist):
    """QA W2: add("005930")은 005930.KS로 저장되는데, remove·contains도 "005930"을 받는다."""
    watchlist.add("005930")
    assert watchlist.contains("005930")
    assert watchlist.remove("005930") is True
    assert watchlist.rows() == []


def test_corrupted_rows_are_purged_and_do_not_use_the_limit(tmp_path, provider):
    """Security W2: 손상된 행은 지울 수 없고 한도만 차지하던 문제."""
    path = tmp_path / "w.db"
    repo = Repository(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute("INSERT INTO watchlist VALUES ('bad symbol!', 'x', 'KRW', '2026-01-01')")
    conn.execute("INSERT INTO watchlist VALUES ('GOOD', 'x', 'EUR', '2026-01-01')")
    conn.commit()
    conn.close()
    watchlist = Watchlist(repo, provider)
    assert watchlist.rows() == []
    assert repo._conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0


def test_stored_value_can_be_removed_even_if_malformed(watchlist):
    watchlist.repo._conn.execute("INSERT INTO watchlist VALUES ('bad symbol!', 'x', 'KRW', '2026-01-01')")
    assert watchlist.remove("bad symbol!") is True


def test_names_are_cleaned_before_storing_even_from_other_providers(provider):
    """Security W1: YFinanceProvider가 아닌 시세 출처가 정리 안 된 이름을 줘도 저장 전에 정리한다."""
    evil = "Evil‮<b>Corp</b>\x00"
    provider.quotes["EVIL"] = Quote("EVIL", evil, Decimal("10"), Currency.USD, Decimal("10"))
    repo = Repository()
    Watchlist(repo, provider).add("EVIL")
    [item] = repo.watchlist()
    assert "‮" not in item.name and "\x00" not in item.name

    broker = Broker(repo, provider)
    broker.open_account(10_000_000)
    trade = broker.buy("EVIL", 1, expected_price=Decimal("10"), expected_fx=Decimal("1300"))
    assert "‮" not in trade.name and "\x00" not in trade.name


def test_rows_use_one_batch_request_when_provider_supports_it(provider):
    """QA W1 / Security W3: 관심 종목 시세는 한 번에 받는다."""
    calls = []

    class BatchProvider(type(provider)):
        def get_quotes(self, items):
            calls.append([s for s, _, _ in items])
            return {s: self.quotes[s] for s, _, _ in items if s in self.quotes}

        def get_quote(self, symbol):
            raise AssertionError("일괄 조회가 있으면 종목별 조회를 하지 않는다")

    batch = BatchProvider()
    repo = Repository()
    for symbol, name, currency in [("AAPL", "Apple", Currency.USD), ("005930.KS", "Samsung", Currency.KRW), ("GONE", "Gone", Currency.USD)]:
        repo.add_watch(symbol, name, currency, limit=50)
    rows = {r.item.symbol: r for r in Watchlist(repo, batch).rows()}
    assert len(calls) == 1 and sorted(calls[0]) == ["005930.KS", "AAPL", "GONE"]  # 요청 한 번
    assert rows["AAPL"].quote is not None and rows["GONE"].quote is None


def test_window_skips_duplicate_watch_refresh_and_only_refreshes_visible_tab(provider):
    """QA W1·W3: 이전 갱신이 진행 중이면 또 넣지 않고, 관심 종목 탭이 안 보이면 자동 갱신하지 않는다."""
    from PySide6.QtWidgets import QApplication

    from mock_trading.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    broker = Broker(Repository(check_same_thread=False), provider)
    broker.open_account(10_000_000)
    window = MainWindow(broker)
    calls = []
    window.watchlist.rows = lambda: calls.append(1) or []

    window.refresh_all()  # 첫 탭(보유 종목)이 보이는 중
    window.worker.wait(3000)
    app.processEvents()
    assert calls == []

    window.tabs.setCurrentWidget(window.watch_tab)  # 탭 전환 시 1회
    window.refresh_all()
    window.refresh_all()                            # 첫 조회가 진행 중일 때 두 번 더 요청해도
    for _ in range(5):                              # 첫 조회 → 끝난 뒤 다시 1회까지 모두 처리
        window.worker.wait(3000)
        window.reader.wait(3000)
        app.processEvents()
    assert len(calls) == 2                          # 겹쳐 넣지 않고, 끝난 뒤 딱 한 번 더(Mock_QA P1)
    assert window._watch_reload is False

    window._watch_pending = True
    window.load_quote("AAPL")
    window.worker.wait(3000)
    app.processEvents()
    assert not window.watch_button.isEnabled()      # 담기·빼기 처리 중에는 다시 켜지지 않음
    window.close()


def test_null_symbol_row_is_purged_and_does_not_block_others(tmp_path, provider):
    """Mock_Sequrity F2: symbol이 NULL인 행도 정리되고, 손상된 코드 하나가 나머지 시세를 막지 않는다."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)  # 이전 스키마(NOT NULL 없음)
    conn.execute("CREATE TABLE watchlist (symbol TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL, added_at TEXT NOT NULL)")
    conn.execute("INSERT INTO watchlist VALUES (NULL, 'x', 'KRW', '2026-01-01')")
    conn.execute("INSERT INTO watchlist VALUES ('AAPL', 'Apple', 'USD', '2026-01-02')")
    conn.commit()
    conn.close()
    repo = Repository(path)
    assert [i.symbol for i in repo.watchlist()] == ["AAPL"]
    assert repo._conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 1


def test_get_quotes_skips_bad_code_without_failing_the_batch(monkeypatch):
    from mock_trading.market_data import YFinanceProvider

    requested = []

    class FakeFrame(dict):
        pass

    class FakeYF:
        @staticmethod
        def download(symbols, **kwargs):
            requested.extend(symbols)
            import pandas as pd
            index = pd.to_datetime(["2026-10-14", "2026-10-15"])
            return {"Close": pd.DataFrame({"AAPL": [100.0, 101.0]}, index=index)}

    provider = YFinanceProvider.__new__(YFinanceProvider)
    provider._yf = FakeYF()
    provider._names = {}
    quotes = provider.get_quotes([("bad symbol!", "x", Currency.USD), ("AAPL", "Apple", Currency.USD)])
    assert requested == ["AAPL"]
    assert quotes["AAPL"].price == Decimal("101.0000")
    assert quotes["AAPL"].previous_close == Decimal("100.0000")
