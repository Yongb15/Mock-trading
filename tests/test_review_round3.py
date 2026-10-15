"""QA·Security 3차 리뷰(5fc0fdc)에서 나온 문제의 회귀 테스트."""
from decimal import Decimal

import pytest

from tests.helpers import AutoConfirmBroker
from mock_trading.broker import Broker, FeePolicy
from mock_trading.domain import Currency, Quote, TradingError
from mock_trading.storage import Repository

from .test_broker import FakeProvider


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def broker(provider):
    b = AutoConfirmBroker(Repository(), provider, FeePolicy(commission_rate=Decimal("0.001"), kr_sell_tax_rate=Decimal("0.002")))
    b.open_account(10_000_000)
    return b


def test_fx_jump_within_range_is_caught_by_expected_fx(broker, provider):
    """Security R1: 1300 → 2600처럼 허용 범위 안에서 튄 환율도 확인창 값과 비교해 막는다."""
    broker.buy("AAPL", 1)
    provider.usd_krw = Decimal("2600")
    with pytest.raises(TradingError, match="환율"):
        broker.sell("AAPL", 1, expected_price=Decimal("200.50"), expected_fx=Decimal("1300"))
    provider.usd_krw = Decimal("1310")  # 0.8% 차이는 허용
    trade = broker.sell("AAPL", 1, expected_price=Decimal("200.50"), expected_fx=Decimal("1300"))
    assert trade.fx_rate == Decimal("1310")


def test_expected_fx_is_ignored_for_krw(broker):
    trade = broker.buy("005930", 1, expected_price=Decimal("70000"), expected_fx=Decimal("1300"))
    assert trade.fx_rate == 1


@pytest.mark.parametrize("price", ["70000", "74000", "130000", "135000"])
def test_krw_limit_moves_are_tradable(broker, provider, price):
    """Security R2: 하한가(-30%)·상한가(+30%) 근처 정상 주문은 막히지 않는다."""
    provider.quotes["005930.KS"] = Quote("005930.KS", "Samsung", Decimal(price), Currency.KRW, Decimal("100000"))
    broker.buy("005930", 1)


@pytest.mark.parametrize("price", ["64000", "136000"])
def test_krw_moves_beyond_band_are_blocked(broker, provider, price):
    provider.quotes["005930.KS"] = Quote("005930.KS", "Samsung", Decimal(price), Currency.KRW, Decimal("100000"))
    with pytest.raises(TradingError, match="비정상"):
        broker.buy("005930", 1)


def test_order_started_before_reset_is_not_recorded_on_new_account(broker, provider):
    """Security R3 / QA R1: 시세를 기다리는 동안 계좌가 초기화되면 이전 주문은 취소된다."""
    original_get_quote = provider.get_quote

    def slow_quote(symbol):
        broker.open_account(5_000_000)  # 네트워크 대기 중에 다른 곳에서 초기화가 일어난 상황
        return original_get_quote(symbol)

    provider.get_quote = slow_quote
    with pytest.raises(TradingError, match="초기화"):
        broker.buy("005930", 1)
    assert broker.repo.trades() == []
    assert broker.repo.cash() == Decimal("5000000")


def test_account_generation_changes_on_every_reset():
    repo = Repository()
    repo.reset_account(Decimal("1000000"))
    first = repo.account_generation()
    repo.reset_account(Decimal("1000000"))
    assert repo.account_generation() != first


def test_hold_for_exit_waits_for_open_transaction(tmp_path):
    """QA R2: 종료할 때 트랜잭션 도중이면 잠금을 잡지 못한다."""
    import threading

    repo = Repository(tmp_path / "x.db", check_same_thread=False)
    repo.reset_account(Decimal("1000000"))
    entered, release = threading.Event(), threading.Event()

    def long_transaction():
        with repo.transaction():
            entered.set()
            release.wait(5)

    t = threading.Thread(target=long_transaction)
    t.start()
    entered.wait(5)
    assert repo.hold_for_exit(timeout=0.1) is False
    release.set()
    t.join()
    assert repo.hold_for_exit(timeout=1) is True
