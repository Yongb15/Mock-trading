from decimal import Decimal

import pytest

from mock_trading.broker import Broker, FeePolicy
from mock_trading.domain import Currency, Quote, Side, TradingError
from mock_trading.market_data import normalize_symbol
from mock_trading.storage import Repository


class FakeProvider:
    def __init__(self) -> None:
        self.quotes = {
            "005930.KS": Quote("005930.KS", "Samsung", Decimal("70000"), Currency.KRW, Decimal("69000")),
            "AAPL": Quote("AAPL", "Apple", Decimal("200.50"), Currency.USD, Decimal("199")),
        }
        self.usd_krw = Decimal("1300")

    def get_quote(self, symbol):
        if symbol == "005930":
            symbol = "005930.KS"
        if symbol not in self.quotes:
            raise TradingError("없음")
        return self.quotes[symbol]

    def get_usd_krw(self):
        return self.usd_krw

    def get_history(self, symbol, period="3mo"):
        return []


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def broker(provider):
    b = Broker(Repository(), provider, FeePolicy(commission_rate=Decimal("0.001"), kr_sell_tax_rate=Decimal("0.002")))
    b.open_account(10_000_000)
    return b


def test_buy_korean_stock_deducts_amount_and_fee(broker):
    trade = broker.buy("005930", 10)
    assert trade.symbol == "005930.KS"
    assert trade.amount_krw == Decimal("700000")
    assert trade.fee_krw == Decimal("700")
    assert broker.repo.cash() == Decimal("10000000") - Decimal("700700")


def test_buy_us_stock_converts_with_fx(broker):
    trade = broker.buy("aapl", 3)
    # 200.50 * 1300 * 3
    assert trade.amount_krw == Decimal("781950")
    assert trade.fx_rate == Decimal("1300")
    holding = broker.repo.holdings()["AAPL"]
    assert holding.quantity == 3
    assert holding.cost_krw == Decimal("781950") + Decimal("781")


def test_cannot_buy_more_than_cash(broker):
    with pytest.raises(TradingError, match="현금이 부족"):
        broker.buy("005930", 1000)
    assert broker.repo.trades() == []


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "3", True, 10_000_001])
def test_rejects_invalid_quantity(broker, quantity):
    with pytest.raises(TradingError):
        broker.buy("005930", quantity)


def test_cannot_sell_more_than_held(broker):
    broker.buy("005930", 5)
    with pytest.raises(TradingError, match="보유 수량이 부족"):
        broker.sell("005930", 6)


def test_sell_applies_kr_tax_and_realizes_profit(broker, provider):
    broker.buy("005930", 10)  # 비용 700,000 + 700
    provider.quotes["005930.KS"] = Quote("005930.KS", "Samsung", Decimal("80000"), Currency.KRW, Decimal("75000"))
    trade = broker.sell("005930", 4)
    assert trade.amount_krw == Decimal("320000")
    assert trade.fee_krw == Decimal("960")  # (0.1% + 0.2%) of 320,000
    holding = broker.repo.holdings()["005930.KS"]
    assert holding.quantity == 6
    assert holding.cost_krw == Decimal("420420")  # 700,700 * 6/10
    # 320,000 - 960 - 280,280
    assert broker.repo.realized_profit() == Decimal("38760")


def test_selling_everything_removes_holding(broker):
    broker.buy("AAPL", 2)
    broker.sell("AAPL", 2)
    assert broker.repo.holdings() == {}
    assert Side.SELL == broker.repo.trades()[-1].side


def test_max_buy_quantity_is_affordable(broker, provider):
    quote = provider.get_quote("005930.KS")
    quantity = broker.max_buy_quantity(quote)
    broker.buy("005930", quantity)
    with pytest.raises(TradingError):
        broker.buy("005930", 1)


def test_summary_values_positions(broker, provider):
    broker.buy("AAPL", 1)
    provider.usd_krw = Decimal("1400")
    summary = broker.summary()
    assert summary.stock_value == Decimal("280700")  # 200.50 * 1400
    assert summary.total == broker.repo.cash() + Decimal("280700")


@pytest.mark.parametrize("amount", [0, -5, "abc", "1.5", 99_999, 10**12, "NaN"])
def test_rejects_invalid_initial_cash(amount):
    broker = Broker(Repository(), FakeProvider())
    with pytest.raises(TradingError):
        broker.open_account(amount)


def test_reset_clears_history(broker):
    broker.buy("005930", 1)
    broker.open_account(5_000_000)
    assert broker.repo.trades() == []
    assert broker.repo.cash() == Decimal("5000000")


@pytest.mark.parametrize("raw", ["", "  ", "AAPL; DROP TABLE", "../etc", "a" * 20])
def test_normalize_symbol_rejects_garbage(raw):
    with pytest.raises(TradingError):
        normalize_symbol(raw)


def test_normalize_symbol_cleans_input():
    assert normalize_symbol(" aapl ") == "AAPL"
    assert normalize_symbol("brk-b") == "BRK-B"
