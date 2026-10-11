"""QA·Security 2차 리뷰(e2035b0/d110606)에서 나온 문제의 회귀 테스트."""
import sqlite3
import threading
from decimal import Decimal

import pytest

from mock_trading.broker import Broker, FeePolicy
from mock_trading.domain import Currency, Quote, TradingError
from mock_trading.storage import LedgerError, Repository

from .test_broker import FakeProvider


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def broker(provider):
    b = Broker(Repository(), provider, FeePolicy(commission_rate=Decimal("0.001"), kr_sell_tax_rate=Decimal("0.002")))
    b.open_account(10_000_000)
    return b


@pytest.mark.parametrize("rate", ["14000", "130", "499.99", "3000.01"])
def test_fx_outside_sane_range_blocks_orders(broker, provider, rate):
    """Security N1-1: 환율이 10배로 잘못 와도 매도 금액이 부풀지 않는다."""
    broker.buy("AAPL", 1)
    provider.usd_krw = Decimal(rate)
    with pytest.raises(TradingError, match="환율"):
        broker.sell("AAPL", 1)
    assert len(broker.repo.trades()) == 1


def test_missing_previous_close_fails_closed(broker, provider):
    """Security N1-2: 전일 종가가 없으면 가격이 정상인지 알 수 없으므로 주문을 막는다."""
    broker.buy("AAPL", 1)
    provider.quotes["AAPL"] = Quote("AAPL", "Apple", Decimal("200500"), Currency.USD, None)
    with pytest.raises(TradingError, match="전일 종가"):
        broker.sell("AAPL", 1)


def test_krw_price_band_is_tighter_than_usd(broker, provider):
    """Security N1-3: 원화 종목은 ±30% 가격제한폭에 맞춰 1.35배까지만."""
    provider.quotes["005930.KS"] = Quote("005930.KS", "Samsung", Decimal("95000"), Currency.KRW, Decimal("70000"))
    with pytest.raises(TradingError, match="비정상"):
        broker.buy("005930", 1)
    provider.quotes["005930.KS"] = Quote("005930.KS", "Samsung", Decimal("91000"), Currency.KRW, Decimal("70000"))
    broker.buy("005930", 1)  # +30%는 통과


def test_order_rejected_when_price_moved_after_confirmation(broker, provider):
    """Security N4: 확인창에서 본 가격과 5% 넘게 다르면 체결하지 않는다."""
    with pytest.raises(TradingError, match="다시 주문"):
        broker.buy("005930", 1, expected_price=Decimal("60000"))  # 실제 70,000
    trade = broker.buy("005930", 1, expected_price=Decimal("69000"))  # 1.4% 차이는 허용
    assert trade.price == Decimal("70000")


def test_one_failing_quote_does_not_hide_the_account(broker, provider):
    """Security N5 / QA C2: 한 종목 시세가 실패해도 나머지와 요약은 보인다."""
    broker.buy("005930", 1)
    broker.buy("AAPL", 1)
    del provider.quotes["AAPL"]  # 상장폐지 흉내
    positions = {p.holding.symbol: p for p in broker.positions()}
    assert positions["005930.KS"].has_price
    assert not positions["AAPL"].has_price
    assert positions["AAPL"].value_krw == positions["AAPL"].holding.cost_krw  # 원가로 평가
    assert broker.summary().cash == broker.repo.cash()


def test_fx_failure_keeps_krw_positions_priced(broker, provider):
    broker.buy("005930", 1)
    broker.buy("AAPL", 1)
    provider.usd_krw = Decimal("NaN")
    positions = {p.holding.symbol: p for p in broker.positions()}
    assert positions["005930.KS"].has_price
    assert not positions["AAPL"].has_price


def test_positions_without_account_is_trading_error(provider):
    """QA U2"""
    with pytest.raises(TradingError, match="계좌"):
        Broker(Repository(), provider).positions()


def test_max_buy_with_zero_price_returns_zero(broker):
    """QA C1: 다른 provider가 0원을 줘도 0으로 나누지 않는다."""
    assert broker.max_buy_quantity(Quote("ZERO", "Zero", Decimal(0), Currency.KRW)) == 0


@pytest.mark.parametrize(
    "column,value",
    [("quantity", "'abc'"), ("executed_at", "'yesterday'"), ("side", "'HOLD_X'"), ("currency", "'EUR'")],
)
def test_any_corrupted_column_becomes_ledger_error(tmp_path, column, value):
    """Security N3: 어떤 칸이 손상돼도 LedgerError(→ 초기화 안내)로 나온다."""
    path = tmp_path / "bad.db"
    repo = Repository(path)
    repo.reset_account(Decimal("1000000"))
    conn = sqlite3.connect(path)
    # 손상 흉내: CHECK 제약을 끄고 직접 값을 넣는다.
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute(
        "INSERT INTO trades (executed_at, symbol, name, side, quantity, price, currency, fx_rate, amount_krw, fee_krw)"
        " VALUES ('2026-01-01T00:00:00', 'X', 'X', 'BUY', 1, '10', 'KRW', '1', '10', '0')"
    )
    conn.execute(f"UPDATE trades SET {column} = {value}")
    conn.commit()
    conn.close()
    with pytest.raises(LedgerError):
        repo.ledger()


def test_schema_rejects_text_quantity():
    repo = Repository()
    with pytest.raises(sqlite3.IntegrityError):
        repo._conn.execute(
            "INSERT INTO trades (executed_at, symbol, name, side, quantity, price, currency, fx_rate, amount_krw, fee_krw)"
            " VALUES ('2026-01-01T00:00:00', 'X', 'X', 'BUY', 'abc', '10', 'KRW', '1', '10', '0')"
        )


def test_repository_serializes_threads(tmp_path, provider):
    """Security N2 / QA U4: 두 스레드가 같은 연결로 동시에 주문·초기화해도 트랜잭션이 겹치지 않는다."""
    repo = Repository(tmp_path / "t.db", check_same_thread=False)
    broker = Broker(repo, provider)
    broker.open_account(100_000_000)
    errors: list[BaseException] = []

    def orders():
        try:
            for _ in range(50):
                broker.buy("005930", 1)
        except TradingError:
            pass  # 초기화 직후 현금 부족 등은 정상
        except BaseException as error:
            errors.append(error)

    def resets():
        try:
            for _ in range(20):
                broker.open_account(100_000_000)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=orders), threading.Thread(target=resets)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    ledger = repo.ledger()
    assert ledger.cash + sum(h.cost_krw for h in ledger.holdings.values()) == repo.initial_cash()
