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
