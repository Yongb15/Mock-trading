import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from mock_trading.domain import Currency, Quote  # noqa: E402
from mock_trading.market_data import Snapshot  # noqa: E402
from mock_trading.market_rank import CACHE_TTL, UNIVERSE, Market, MarketRanking, RankBy, rank  # noqa: E402


def snap(symbol, price, previous, volume=None, currency=Currency.KRW):
    return Snapshot(Quote(symbol, symbol, Decimal(price), currency, Decimal(previous) if previous else None), volume)


SNAPS = [
    snap("A", "110", "100", 500),     # +10%
    snap("B", "95", "100", 900),      # -5%
    snap("C", "102", "100", None),    # +2%, 거래량 모름
    snap("D", "100", "100", 100),     # 0%
    snap("E", "80", "100", 300),      # -20%
    snap("F", "100", None, 700),      # 등락률 모름
]


def test_gainers_only_rising_sorted_desc():
    rows = rank(SNAPS, RankBy.GAINERS)
    assert [r.symbol for r in rows] == ["A", "C"]
    assert [r.rank for r in rows] == [1, 2]


def test_losers_only_falling_sorted_by_drop():
    assert [r.symbol for r in rank(SNAPS, RankBy.LOSERS)] == ["E", "B"]


def test_volume_skips_unknown_volume():
    assert [r.symbol for r in rank(SNAPS, RankBy.VOLUME)] == ["B", "F", "A", "E", "D"]


def test_top_n_limit():
    many = [snap(f"S{i}", str(100 + i), "100", i) for i in range(1, 40)]
    assert len(rank(many, RankBy.GAINERS, top=20)) == 20


class CountingProvider:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def get_snapshots(self, items):
        self.calls.append(len(items))
        if self.fail:
            return {}
        return {s: snap(s, "110", "100", 1, currency) for s, _, currency in items}


def test_cache_reuses_quotes_when_switching_criteria():
    provider = CountingProvider()
    ranking = MarketRanking(provider)
    now = datetime(2026, 10, 16, 10)
    ranking.ranking(Market.KR, RankBy.GAINERS, now=now)
    ranking.ranking(Market.KR, RankBy.VOLUME, now=now + timedelta(minutes=1))  # 기준만 바꿈 → 다시 안 받음
    assert provider.calls == [len(UNIVERSE[Market.KR])]
    ranking.ranking(Market.US, RankBy.GAINERS, now=now)                       # 다른 시장은 따로
    ranking.ranking(Market.KR, RankBy.GAINERS, now=now + CACHE_TTL)           # 5분 지나면 다시
    assert len(provider.calls) == 3
    ranking.ranking(Market.KR, RankBy.GAINERS, now=now + CACHE_TTL, force=True)
    assert len(provider.calls) == 4


def test_empty_fetch_keeps_previous_result():
    provider = CountingProvider()
    ranking = MarketRanking(provider)
    now = datetime(2026, 10, 16, 10)
    first = ranking.ranking(Market.US, RankBy.GAINERS, now=now)
    provider.fail = True
    again = ranking.ranking(Market.US, RankBy.GAINERS, now=now + CACHE_TTL)
    assert again.priced == first.priced > 0


def test_universe_codes_are_valid_symbols():
    from mock_trading.market_data import is_valid_symbol

    for market, items in UNIVERSE.items():
        symbols = [s for s, _ in items]
        assert len(symbols) == len(set(symbols))
        assert all(is_valid_symbol(s) for s in symbols)
        if market is Market.KR:
            assert all(s.endswith((".KS", ".KQ")) for s in symbols)


def test_rank_page_shows_rows_and_opens_stock():
    from PySide6.QtWidgets import QApplication

    from mock_trading.broker import Broker
    from mock_trading.storage import Repository
    from mock_trading.ui.main_window import MainWindow

    from .test_broker import FakeProvider

    app = QApplication.instance() or QApplication([])
    provider = FakeProvider()
    broker = Broker(Repository(check_same_thread=False), provider)
    broker.open_account(10_000_000)
    window = MainWindow(broker)
    window.market_ranking.provider = CountingProvider()

    def settle():
        for _ in range(4):
            window.reader.wait(3000)
            window.worker.wait(3000)
            deadline = time.time() + 0.1
            while time.time() < deadline:
                app.processEvents()

    window.tabs.setCurrentWidget(window.rank_page)   # 페이지를 열면 불러온다
    settle()
    assert window.rank_table.rowCount() == 20
    assert "주요 종목" in window.rank_info.text() and "시장 전체 순위가 아님" in window.rank_info.text()
    us = [b for b in window.rank_market_group.buttons() if b.text() == "미국"][0]
    us.click()
    settle()
    assert window.rank_table.item(0, 3).text().startswith("$")
    window._open_ranked(0)
    assert window.tabs.currentWidget() is window.order_page
    assert window.symbol_input.text() == window.rank_table.item(0, 2).text()
    window.close()
