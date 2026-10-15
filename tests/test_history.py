from datetime import date, datetime, timedelta
from decimal import Decimal

from mock_trading.domain import Currency, Side, Trade
from mock_trading.history import MAX_HISTORY_DAYS, asset_history, load_asset_history
from mock_trading.storage import Repository

D = date(2026, 10, 1)


def trade(id, day, symbol, side, qty, price, currency=Currency.KRW, fx="1", fee="0"):
    amount = Decimal(price) * Decimal(fx) * qty
    return Trade(id, datetime.combine(day, datetime.min.time()).replace(hour=10), symbol, symbol, side, qty,
                 Decimal(price), currency, Decimal(fx), amount, Decimal(fee))


def test_no_trades_is_flat_initial_cash():
    points = asset_history(initial_cash=Decimal(1_000_000), trades=[], start=D, end=D + timedelta(days=2),
                           closes={}, usd_krw={})
    assert [p.total for p in points] == [Decimal(1_000_000)] * 3
    assert all(p.return_rate == 0 for p in points)


def test_values_follow_daily_closes_and_forward_fill_holidays():
    trades = [trade(1, D, "A.KS", Side.BUY, 10, "1000", fee="10")]
    closes = {"A.KS": {D: Decimal(1000), D + timedelta(days=1): Decimal(1100), D + timedelta(days=3): Decimal(900)}}
    points = asset_history(initial_cash=Decimal(100_000), trades=trades, start=D, end=D + timedelta(days=3),
                           closes=closes, usd_krw={})
    cash = Decimal(100_000 - 10_000 - 10)
    assert [p.cash for p in points] == [cash] * 4
    # 3일째(휴장)는 2일째 종가 1100을 그대로 쓴다
    assert [p.stock_value for p in points] == [Decimal(10_000), Decimal(11_000), Decimal(11_000), Decimal(9_000)]
    assert points[1].return_rate == Decimal(990) / Decimal(100_000) * 100


def test_trades_apply_from_their_own_day():
    trades = [
        trade(1, D, "A.KS", Side.BUY, 10, "1000"),
        trade(2, D + timedelta(days=2), "A.KS", Side.SELL, 4, "1200", fee="5"),
    ]
    closes = {"A.KS": {D + timedelta(days=n): Decimal(1000 + 100 * n) for n in range(3)}}
    points = asset_history(initial_cash=Decimal(100_000), trades=trades, start=D, end=D + timedelta(days=2),
                           closes=closes, usd_krw={})
    assert points[1].stock_value == Decimal(11_000)              # 10주 * 1100
    assert points[2].cash == Decimal(90_000 + 4_800 - 5)
    assert points[2].stock_value == Decimal(7_200)               # 6주 * 1200


def test_us_stock_uses_that_days_fx():
    trades = [trade(1, D, "AAPL", Side.BUY, 2, "100", Currency.USD, fx="1300")]
    closes = {"AAPL": {D: Decimal(100), D + timedelta(days=1): Decimal(110)}}
    fx = {D: Decimal(1300), D + timedelta(days=1): Decimal(1400)}
    points = asset_history(initial_cash=Decimal(1_000_000), trades=trades, start=D, end=D + timedelta(days=1),
                           closes=closes, usd_krw=fx)
    assert points[1].stock_value == Decimal(110 * 1400 * 2)


def test_missing_prices_fall_back_to_last_trade_price_and_are_flagged():
    trades = [trade(1, D, "NEW", Side.BUY, 3, "500")]
    points = asset_history(initial_cash=Decimal(10_000), trades=trades, start=D, end=D, closes={}, usd_krw={})
    assert points[0].stock_value == Decimal(1_500)
    assert points[0].missing_price is True
    assert points[0].total == Decimal(10_000)


def test_last_point_matches_ledger_cash():
    repo = Repository()
    repo.reset_account(Decimal(1_000_000))
    repo.add_trade(symbol="A.KS", name="A", side=Side.BUY, quantity=3, price=Decimal(1000), currency=Currency.KRW,
                   fx_rate=Decimal(1), amount_krw=Decimal(3000), fee_krw=Decimal(1))
    repo.add_trade(symbol="A.KS", name="A", side=Side.SELL, quantity=1, price=Decimal(1500), currency=Currency.KRW,
                   fx_rate=Decimal(1), amount_krw=Decimal(1500), fee_krw=Decimal(4))

    class NoHistory:
        pass

    points = load_asset_history(repo, NoHistory(), date.today())
    assert points[-1].cash == repo.cash()
    assert points[0].day == repo.account_opened_on()


def test_range_is_capped():
    end = D
    points = asset_history(initial_cash=Decimal(1), trades=[], start=end - timedelta(days=10_000), end=end,
                           closes={}, usd_krw={})
    assert len(points) == MAX_HISTORY_DAYS + 1


def test_provider_failure_still_draws(monkeypatch):
    repo = Repository()
    repo.reset_account(Decimal(1_000_000))
    repo.add_trade(symbol="A.KS", name="A", side=Side.BUY, quantity=1, price=Decimal(1000), currency=Currency.KRW,
                   fx_rate=Decimal(1), amount_krw=Decimal(1000), fee_krw=Decimal(0))

    class Broken:
        def get_daily_closes(self, *a, **k):
            raise RuntimeError("network")

    points = load_asset_history(repo, Broken(), date.today())
    assert points[-1].total == Decimal(1_000_000)


def test_yfinance_provider_daily_closes_parses_and_drops_bad_values():
    """YFinanceProvider에 실제로 붙어 있는지(인터페이스 클래스에 잘못 들어가지 않았는지)와 파싱을 확인한다."""
    import pandas as pd

    from mock_trading.market_data import USD_KRW_SYMBOL, YFinanceProvider

    calls = []

    class FakeYF:
        @staticmethod
        def download(tickers, **kwargs):
            calls.append((list(tickers), kwargs))
            index = pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05"])
            return {"Close": pd.DataFrame({
                "A.KS": [1000.0, float("nan"), -5.0],
                "AAPL": [100.5, 101.0, float("inf")],
                USD_KRW_SYMBOL: [1350.123, 1360.0, 1355.0],
            }, index=index)}

    provider = YFinanceProvider.__new__(YFinanceProvider)
    provider._yf = FakeYF()
    closes, fx = provider.get_daily_closes(["A.KS", "AAPL"], D, include_usd_krw=True)
    assert closes["A.KS"] == {D: Decimal("1000.0")}
    assert closes["AAPL"] == {D: Decimal("100.5"), D + timedelta(days=1): Decimal("101.0")}
    assert fx[D] == Decimal("1350.12")
    assert calls[0][1]["threads"] == 4


# ---------------------------------------------------------------- 리뷰(8259fbf) 반영
from mock_trading.history import DOWNLOAD_BUFFER_DAYS, REFRESH_INTERVAL, DailyCloseCache  # noqa: E402


def test_clock_rolled_back_does_not_inflate_assets():
    """Mock_QA H1: 시계를 되돌려 매도가 매수보다 이른 날짜로 기록돼도 장부 순서(id)대로 반영한다."""
    buy = trade(1, D + timedelta(days=2), "A.KS", Side.BUY, 10, "1000")
    sell = trade(2, D, "A.KS", Side.SELL, 10, "1000")  # 날짜만 거꾸로
    closes = {"A.KS": {D + timedelta(days=n): Decimal(1000) for n in range(4)}}
    points = asset_history(initial_cash=Decimal(100_000), trades=[sell, buy], start=D, end=D + timedelta(days=3),
                           closes=closes, usd_krw={})
    assert all(p.total == Decimal(100_000) for p in points)  # 사고판 값이 같으니 자산은 그대로여야 한다


class CountingProvider:
    def __init__(self):
        self.calls = []

    def get_daily_closes(self, symbols, start, *, include_usd_krw):
        self.calls.append((tuple(symbols), start, include_usd_krw))
        closes = {s: {start + timedelta(days=n): Decimal(100 + n) for n in range(3)} for s in symbols}
        fx = {start: Decimal(1300)} if include_usd_krw else {}
        return closes, fx


def test_cache_downloads_history_once_and_only_recent_days_later():
    """Mock_Sequrity A1: 과거 종가는 한 번만 받고, 10분이 지나면 최근 며칠만 다시 받는다."""
    provider, cache = CountingProvider(), DailyCloseCache()
    now = datetime(2026, 10, 15, 12, 0)
    today = now.date()
    cache.get(provider, ["A.KS", "AAPL"], D, include_fx=True, today=today, now=now)
    cache.get(provider, ["A.KS", "AAPL"], D, include_fx=True, today=today, now=now + timedelta(minutes=5))
    assert len(provider.calls) == 1                                   # 5분 뒤: 네트워크 없음
    cache.get(provider, ["A.KS", "AAPL"], D, include_fx=True, today=today, now=now + REFRESH_INTERVAL)
    assert provider.calls[1][1] == today - timedelta(days=7)          # 10분 뒤: 최근 7일만
    cache.get(provider, ["A.KS", "AAPL", "TSLA"], D, include_fx=True, today=today, now=now + REFRESH_INTERVAL)
    assert provider.calls[2] == (("TSLA",), D, False)                 # 새 종목만 전체 기간


def test_cache_keeps_old_values_when_download_fails():
    provider, cache = CountingProvider(), DailyCloseCache()
    now = datetime(2026, 10, 15, 12, 0)
    first, _ = cache.get(provider, ["A.KS"], D, include_fx=False, today=now.date(), now=now)

    def broken(*a, **k):
        raise RuntimeError("429")

    provider.get_daily_closes = broken
    again, _ = cache.get(provider, ["A.KS"], D, include_fx=False, today=now.date(), now=now + REFRESH_INTERVAL)
    assert again == first


def test_download_start_is_capped_and_buffered():
    """Mock_Sequrity A2 / Mock_QA H4: 손상된 아주 옛날 개설일이어도 3년 + 버퍼까지만 받는다."""
    repo = Repository()
    repo.reset_account(Decimal(1_000_000))
    repo._conn.execute("UPDATE account SET created_at = '0001-01-01T00:00:00'")
    repo.add_trade(symbol="A.KS", name="A", side=Side.BUY, quantity=1, price=Decimal(100), currency=Currency.KRW,
                   fx_rate=Decimal(1), amount_krw=Decimal(100), fee_krw=Decimal(0))
    provider = CountingProvider()
    today = date(2026, 10, 15)
    points = load_asset_history(repo, provider, today, DailyCloseCache(), datetime(2026, 10, 15, 12))
    assert provider.calls[0][1] == today - timedelta(days=MAX_HISTORY_DAYS + DOWNLOAD_BUFFER_DAYS)
    assert len(points) == MAX_HISTORY_DAYS + 1


def test_future_open_date_is_treated_as_today():
    repo = Repository()
    repo.reset_account(Decimal(1_000_000))
    repo._conn.execute("UPDATE account SET created_at = '2999-01-01T00:00:00'")
    points = load_asset_history(repo, CountingProvider(), date(2026, 10, 15))
    assert [p.day for p in points] == [date(2026, 10, 15)]


def test_fx_outliers_are_dropped():
    """Mock_Sequrity A3"""
    import pandas as pd

    from mock_trading.market_data import USD_KRW_SYMBOL, YFinanceProvider

    class FakeYF:
        @staticmethod
        def download(tickers, **kwargs):
            index = pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05"])
            return {"Close": pd.DataFrame({"AAPL": [1.0, 1.0, 1.0], USD_KRW_SYMBOL: [1350.0, 13500.0, 120.0]}, index=index)}

    provider = YFinanceProvider.__new__(YFinanceProvider)
    provider._yf = FakeYF()
    _, fx = provider.get_daily_closes(["AAPL"], D, include_usd_krw=True)
    assert fx == {D: Decimal("1350.00")}


def test_single_point_chart_and_best_worst_ignore_fallback_prices():
    """Mock_QA H2·H3, Mock_Sequrity A3: 점이 하나여도 보이게, 최고·최저는 종가로 평가한 날만."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from mock_trading.broker import Broker
    from mock_trading.history import AssetPoint
    from mock_trading.ui.main_window import MainWindow

    from .test_broker import FakeProvider

    app = QApplication.instance() or QApplication([])
    broker = Broker(Repository(check_same_thread=False), FakeProvider())
    broker.open_account(1_000_000)
    window = MainWindow(broker)
    one = [AssetPoint(D, Decimal(1_000_000), Decimal(0), Decimal(1_000_000))]
    window._show_asset_history(one)
    total = window.history_chart.series()[1]
    assert total.count() == 1 and total.pointsVisible()

    points = [
        AssetPoint(D, Decimal(900_000), Decimal(100_000), Decimal(1_000_000)),
        AssetPoint(D + timedelta(days=1), Decimal(900_000), Decimal(900_000), Decimal(1_000_000), missing_price=True),
        AssetPoint(D + timedelta(days=2), Decimal(900_000), Decimal(150_000), Decimal(1_000_000)),
    ]
    window._show_asset_history(points)
    text = window.history_label.text()
    assert "최고 1,050,000원" in text and "1,800,000" not in text
    window.close()
