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
