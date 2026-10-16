import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from mock_trading.domain import Currency, Quote  # noqa: E402
from mock_trading.market_data import Snapshot  # noqa: E402
from mock_trading.market_rank import (  # noqa: E402
    CACHE_TTL, FORCE_MIN_INTERVAL, RETRY_INTERVAL, UNIVERSE, Market, MarketRanking, RankBy, rank,
)


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
    ranking.ranking(Market.KR, RankBy.GAINERS, now=now + CACHE_TTL, force=True)   # 방금 받았으면 force도 대기
    assert len(provider.calls) == 3
    ranking.ranking(Market.KR, RankBy.GAINERS, now=now + CACHE_TTL + FORCE_MIN_INTERVAL, force=True)
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



class FlakyProvider:
    """처음 몇 번은 비거나 예외, 그다음부터 정상."""
    def __init__(self, bad=("empty",)):
        self.calls, self.bad = 0, list(bad)

    def get_snapshots(self, items):
        self.calls += 1
        if self.bad:
            kind = self.bad.pop(0)
            if kind == "error":
                raise RuntimeError("429")
            return {}
        return {s: snap(s, "110", "100", 1, currency) for s, _, currency in items}


def test_empty_first_fetch_is_not_cached_and_retries_after_a_minute():
    """Mock_QA M1: 첫 조회가 비면 5분 동안 빈 표가 아니라, 1분 뒤 다시 시도해서 순위가 보인다."""
    provider, ranking = FlakyProvider(), None
    ranking = MarketRanking(provider)
    now = datetime(2026, 10, 16, 10)
    first = ranking.ranking(Market.KR, RankBy.GAINERS, now=now)
    assert first.priced == 0 and first.last_failed
    again = ranking.ranking(Market.KR, RankBy.GAINERS, now=now + timedelta(seconds=30))
    assert provider.calls == 1 and again.priced == 0          # 1분 안에는 다시 두드리지 않음
    later = ranking.ranking(Market.KR, RankBy.GAINERS, now=now + RETRY_INTERVAL)
    assert provider.calls == 2 and later.priced > 0 and later.rows


def test_failures_do_not_hammer_the_provider():
    """Mock_Sequrity MR1: 실패(빈 응답·예외)해도 60초 갱신마다 다시 요청하지 않는다."""
    provider = FlakyProvider(bad=["error"] * 100)
    ranking = MarketRanking(provider)
    now = datetime(2026, 10, 16, 10)
    for second in range(0, 600, 10):  # 10분 동안 10초마다 요청
        ranking.ranking(Market.US, RankBy.GAINERS, now=now + timedelta(seconds=second))
    assert provider.calls == 10       # 1분에 한 번


def test_failed_refresh_keeps_showing_old_values():
    provider = FlakyProvider(bad=[])
    ranking = MarketRanking(provider)
    now = datetime(2026, 10, 16, 10)
    ok = ranking.ranking(Market.US, RankBy.GAINERS, now=now)
    provider.bad = ["error"]
    stale = ranking.ranking(Market.US, RankBy.GAINERS, now=now + CACHE_TTL)
    assert stale.priced == ok.priced and stale.last_failed and stale.fetched_at == now


def test_force_has_a_minimum_gap():
    """Mock_Sequrity MR2: "새로 받기" 연타는 30초에 한 번만."""
    provider = FlakyProvider(bad=[])
    ranking = MarketRanking(provider)
    now = datetime(2026, 10, 16, 10)
    for second in range(0, 30, 3):
        ranking.ranking(Market.KR, RankBy.GAINERS, now=now + timedelta(seconds=second), force=True)
    assert provider.calls == 1


def test_ranking_reports_which_close_it_uses():
    """Mock_QA M2: 받은 시각이 아니라 등락률의 기준 종가 날짜를 알려 준다."""
    from datetime import date

    class Dated:
        def get_snapshots(self, items):
            return {s: Snapshot(Quote(s, s, Decimal("110"), currency, Decimal("100")), 1, date(2026, 10, 15))
                    for s, _, currency in items}

    result = MarketRanking(Dated()).ranking(Market.KR, RankBy.GAINERS, now=datetime(2026, 10, 16, 8))
    assert result.as_of == date(2026, 10, 15)


def test_force_wait_tells_how_long_until_refresh_is_allowed():
    """Mock_QA C3: "새로 받기"를 너무 빨리 다시 누르면 몇 초 남았는지 알려 줄 수 있다."""
    ranking = MarketRanking(FlakyProvider(bad=[]))
    now = datetime(2026, 10, 16, 10)
    assert ranking.force_wait(Market.KR, now=now) == 0
    ranking.ranking(Market.KR, RankBy.GAINERS, now=now)
    assert ranking.force_wait(Market.KR, now=now + timedelta(seconds=10)) == 20
    assert ranking.force_wait(Market.KR, now=now + FORCE_MIN_INTERVAL) == 0


def test_retry_interval_is_shorter_than_the_refresh_timer():
    """타이머(60초)가 조금 일찍 와도 재시도가 한 주기 밀리지 않게."""
    from mock_trading.ui.main_window import AUTO_REFRESH_MS

    assert RETRY_INTERVAL.total_seconds() * 1000 < AUTO_REFRESH_MS
