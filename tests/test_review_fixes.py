"""QA·Security 리뷰(358ba6a)에서 나온 문제의 회귀 테스트."""
import sqlite3
from decimal import Decimal

import pytest

from mock_trading.broker import Broker
from mock_trading.domain import MAX_ORDER_QUANTITY, Currency, Quote, Side, TradingError, clean_name, to_decimal
from mock_trading.market_data import normalize_symbol
from mock_trading.storage import LedgerError, Repository

from .test_broker import FakeProvider


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def broker(provider):
    from mock_trading.broker import FeePolicy

    b = Broker(Repository(), provider, FeePolicy(commission_rate=Decimal("0.001"), kr_sell_tax_rate=Decimal("0.002")))
    b.open_account(10_000_000)
    return b


def _set_price(provider, symbol, price, currency=Currency.KRW, previous=None):
    provider.quotes[symbol] = Quote(symbol, symbol, Decimal(price), currency, previous)


def test_max_buy_finds_the_last_affordable_share(provider):
    """QA#1: 1001원 주식, 현금 100,115원 → 100주(100,100 + 수수료 15)까지 살 수 있다."""
    broker = Broker(Repository(), provider)  # 기본 수수료 0.015%
    broker.repo.reset_account(Decimal("100115"))
    _set_price(provider, "005930.KS", "1001")
    assert broker.max_buy_quantity(provider.get_quote("005930.KS")) == 100
    broker.buy("005930", 100)
    assert broker.repo.cash() == 0


def test_default_fee_policy(provider):
    broker = Broker(Repository(), provider)
    broker.open_account(10_000_000)
    assert broker.buy("005930", 10).fee_krw == Decimal("105")    # 700,000 * 0.015%
    assert broker.sell("005930", 10).fee_krw == Decimal("1505")  # 700,000 * (0.015% + 0.20%)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), Decimal("NaN"), Decimal("-Infinity")])
def test_nan_or_inf_fx_never_reaches_the_ledger(broker, provider, bad):
    """Security H1 / QA#2: 환율이 NaN이면 매도도 막히고 장부는 그대로여야 한다."""
    broker.buy("AAPL", 1)
    provider.usd_krw = bad
    with pytest.raises(TradingError):
        broker.sell("AAPL", 1)
    with pytest.raises(TradingError):
        broker.buy("AAPL", 1)
    assert len(broker.repo.trades()) == 1
    assert broker.repo.cash().is_finite()


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "0", "-1"])
def test_bad_price_is_rejected(broker, provider, bad):
    """Security M3: 비정상 시세는 TradingError로 막고 InvalidOperation이 새어 나가지 않는다."""
    _set_price(provider, "005930.KS", bad)
    with pytest.raises(TradingError):
        broker.buy("005930", 1)


def test_sub_cent_us_price_is_not_rounded_to_zero(broker, provider):
    """Security H2: 0.004달러를 0.00으로 반올림해 공짜로 사는 일이 없어야 한다."""
    _set_price(provider, "PENNY", "0.004", Currency.USD)
    trade = broker.buy("PENNY", 1_000_000)
    assert trade.price == Decimal("0.0040")
    assert trade.amount_krw == Decimal("5200000")  # 0.004 * 1300 * 1,000,000
    # 사자마자 평가하면 수수료만큼만 손해다. 반올림 차익이 없다.
    assert broker.positions()[0].profit_krw == -trade.fee_krw


def test_price_too_small_to_trade_is_rejected(broker, provider):
    _set_price(provider, "DUST", "0.00004", Currency.USD)
    with pytest.raises(TradingError, match="가격이 너무 낮아"):
        broker.buy("DUST", 1)


def test_wild_price_jump_blocks_order(broker, provider):
    _set_price(provider, "005930.KS", "700000", previous=Decimal("70000"))
    with pytest.raises(TradingError, match="비정상"):
        broker.buy("005930", 1)


def test_fx_is_stored_rounded(broker, provider):
    """QA#5: 환율은 float repr 그대로가 아니라 0.01 단위로 저장한다."""
    provider.usd_krw = 1385.239990234375
    assert broker.buy("AAPL", 1).fx_rate == Decimal("1385.24")


@pytest.mark.parametrize("raw", ["1E+5", "100000.0", 100000.0, Decimal("1.00000E+5")])
def test_initial_cash_is_stored_as_plain_integer(raw):
    """QA#3"""
    repo = Repository()
    Broker(repo, FakeProvider()).open_account(raw)
    assert str(repo.initial_cash()) == "100000"
    assert f"{repo.cash():,}" == "100,000"


def test_orders_without_account_raise_trading_error(provider):
    """QA#4"""
    broker = Broker(Repository(), provider)
    with pytest.raises(TradingError, match="계좌"):
        broker.buy("005930", 1)
    with pytest.raises(TradingError, match="계좌"):
        broker.max_buy_quantity(provider.get_quote("005930.KS"))
    with pytest.raises(TradingError, match="계좌"):
        broker.summary()


def test_moving_average_cost_across_buys(broker, provider):
    broker.buy("005930", 10)                       # 700,000 + 700
    _set_price(provider, "005930.KS", "90000")
    broker.buy("005930", 10)                       # 900,000 + 900
    holding = broker.repo.holdings()["005930.KS"]
    assert holding.cost_krw == Decimal("1601600")
    assert holding.avg_cost_krw == Decimal("80080")
    broker.sell("005930", 5)                       # 450,000 - 1,350
    assert broker.repo.holdings()["005930.KS"].cost_krw == Decimal("1201200")
    assert broker.repo.realized_profit() == Decimal("450000") - 1350 - Decimal("400400")


def test_uneven_partial_sells_leak_no_rounding(broker, provider):
    _set_price(provider, "005930.KS", "33333")
    broker.buy("005930", 3)
    _set_price(provider, "005930.KS", "40001")
    sells = [broker.sell("005930", 1) for _ in range(3)]
    buy = broker.repo.trades()[0]
    proceeds = sum(t.amount_krw - t.fee_krw for t in sells)
    assert broker.repo.realized_profit() == proceeds - (buy.amount_krw + buy.fee_krw)
    assert broker.repo.holdings() == {}


def test_us_sell_has_no_tax_and_includes_fx_gain(broker, provider):
    buy = broker.buy("AAPL", 2)                    # 1300원/달러
    provider.usd_krw = Decimal("1400")
    sell = broker.sell("AAPL", 2)
    assert sell.amount_krw == Decimal("561400")    # 200.50 * 1400 * 2
    assert sell.fee_krw == Decimal("561")          # 수수료 0.1%만, 거래세 없음
    assert broker.repo.realized_profit() == sell.amount_krw - sell.fee_krw - buy.amount_krw - buy.fee_krw


def test_ledger_invariant(broker, provider):
    """초기자금 + 실현손익 - 남은 보유 원가 == 현금"""
    broker.buy("005930", 7)
    broker.buy("AAPL", 3)
    _set_price(provider, "005930.KS", "75555")
    broker.sell("005930", 4)
    provider.usd_krw = Decimal("1333.33")
    broker.sell("AAPL", 1)
    ledger = broker.repo.ledger()
    remaining_cost = sum(h.cost_krw for h in ledger.holdings.values())
    assert broker.repo.initial_cash() + ledger.realized_profit - remaining_cost == ledger.cash


def test_quantity_boundary(provider):
    broker = Broker(Repository(), provider)
    broker.open_account(100_000_000_000)
    _set_price(provider, "005930.KS", "100")
    broker.buy("005930", MAX_ORDER_QUANTITY)
    with pytest.raises(TradingError):
        broker.buy("005930", MAX_ORDER_QUANTITY + 1)


def test_file_db_persists_and_rolls_back(tmp_path, provider):
    path = tmp_path / "ledger.db"
    broker = Broker(Repository(path), provider)
    broker.open_account(10_000_000)
    broker.buy("005930", 2)
    with pytest.raises(RuntimeError):
        with broker.repo.transaction():
            broker.repo.add_trade(
                symbol="AAPL", name="Apple", side=Side.BUY, quantity=1, price=Decimal("1"),
                currency=Currency.USD, fx_rate=Decimal("1300"), amount_krw=Decimal("1300"), fee_krw=Decimal("0"),
            )
            raise RuntimeError("중간 실패")
    broker.repo.close()

    reopened = Repository(path)
    assert [t.symbol for t in reopened.trades()] == ["005930.KS"]
    assert reopened.cash() == Decimal("10000000") - Decimal("140021")  # 140,000 + 수수료 21


@pytest.mark.parametrize("field,value", [("price", Decimal("NaN")), ("amount_krw", Decimal("0")), ("fee_krw", Decimal("-1"))])
def test_add_trade_is_a_last_line_of_defense(field, value):
    repo = Repository()
    repo.reset_account(Decimal("1000000"))
    kwargs = dict(symbol="X", name="X", side=Side.BUY, quantity=1, price=Decimal("10"), currency=Currency.KRW,
                  fx_rate=Decimal("1"), amount_krw=Decimal("10"), fee_krw=Decimal("0"))
    kwargs[field] = value
    with pytest.raises(TradingError):
        repo.add_trade(**kwargs)


def test_corrupted_ledger_raises_ledger_error(tmp_path):
    """Security L5: 보유보다 많이 판 기록이 있으면 0으로 나누기 대신 LedgerError."""
    path = tmp_path / "bad.db"
    repo = Repository(path)
    repo.reset_account(Decimal("1000000"))
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO trades (executed_at, symbol, name, side, quantity, price, currency, fx_rate, amount_krw, fee_krw)"
        " VALUES ('2026-01-01T00:00:00', 'X', 'X', 'SELL', 1, '10', 'KRW', '1', '10', '0')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(LedgerError):
        repo.realized_profit()


def test_to_decimal_rejects_non_finite():
    for bad in [float("nan"), float("inf"), "NaN", "abc", None, True]:
        with pytest.raises(TradingError):
            to_decimal(bad)


def test_clean_name_strips_control_chars_and_truncates():
    """Security M4"""
    assert clean_name("Apple\x00\n Inc.", "AAPL") == "Apple Inc."
    assert clean_name(None, "AAPL") == "AAPL"
    assert clean_name({"x": 1}, "AAPL") == "AAPL"
    assert len(clean_name("A" * 500, "X")) == 100


def test_normalize_symbol_strict():
    """Security L7"""
    assert normalize_symbol("AAPL\n") == "AAPL"
    for bad in [None, 123, "AAP L", "AAPL\nX"]:
        with pytest.raises(TradingError):
            normalize_symbol(bad)
