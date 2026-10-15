from decimal import Decimal

import pytest

from mock_trading.broker import MAX_OPEN_ORDERS, Broker, FeePolicy
from mock_trading.domain import Currency, OrderStatus, Quote, Side, TradingError
from mock_trading.storage import Repository

from .test_broker import FakeProvider


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def broker(provider):
    b = Broker(Repository(), provider, FeePolicy(commission_rate=Decimal("0.001"), kr_sell_tax_rate=Decimal("0.002")))
    b.open_account(10_000_000)
    return b


def set_price(provider, symbol, price, currency=Currency.KRW):
    provider.quotes[symbol] = Quote(symbol, symbol, Decimal(price), currency, Decimal(price))


def check_twice(broker, now=None):
    """체결은 두 번 연속 조건이 맞아야 한다: 첫 확인은 표시만, 두 번째 확인의 결과를 돌려준다."""
    first = broker.check_limit_orders(now)
    assert first == []
    return broker.check_limit_orders(now)


def test_buy_waits_until_price_falls_to_limit_and_fills_at_limit_price(broker, provider):
    order = broker.place_limit_order(Side.BUY, "005930", 10, Decimal("65000"))  # 현재 70,000
    assert order.status is OrderStatus.OPEN and order.symbol == "005930.KS"
    assert broker.check_limit_orders() == []
    set_price(provider, "005930.KS", "64000")
    [event] = check_twice(broker)
    assert event.trade.price == Decimal("65000") and event.trade.side is Side.BUY  # Mock_Sequrity LO1(a): 지정가로
    [stored] = broker.repo.orders()
    assert stored.status is OrderStatus.FILLED and stored.trade_id == event.trade.id
    assert broker.repo.cash() == Decimal(10_000_000) - Decimal(650_000) - Decimal(650)


def test_single_bad_tick_does_not_fill(broker, provider):
    """조건이 한 번만 맞고 다음 확인에서 풀리면 체결하지 않고 확인 표시도 지운다."""
    broker.place_limit_order(Side.BUY, "005930", 1, Decimal("65000"))
    set_price(provider, "005930.KS", "64000")
    assert broker.check_limit_orders() == []
    assert broker.repo.orders(open_only=True)[0].armed is True
    set_price(provider, "005930.KS", "70000")
    assert broker.check_limit_orders() == []
    assert broker.repo.orders(open_only=True)[0].armed is False
    assert broker.repo.trades() == []


def test_sell_fills_when_price_rises_to_limit(broker, provider):
    broker.buy("005930", 5, expected_price=Decimal("70000"))
    broker.place_limit_order(Side.SELL, "005930", 5, Decimal("75000"))
    set_price(provider, "005930.KS", "74999")
    assert broker.check_limit_orders() == []
    set_price(provider, "005930.KS", "76000")
    [event] = check_twice(broker)
    assert event.trade.side is Side.SELL and event.trade.price == Decimal("75000")
    assert broker.repo.holdings() == {}


def test_filled_order_is_not_filled_twice(broker, provider):
    broker.place_limit_order(Side.BUY, "005930", 1, Decimal("70000"))
    check_twice(broker)
    assert broker.check_limit_orders() == []
    assert len(broker.repo.trades()) == 1


def test_us_limit_order_uses_fx_at_fill(broker, provider):
    broker.place_limit_order(Side.BUY, "AAPL", 2, Decimal("200"))  # 현재 200.50, 주문 환율 1300
    set_price(provider, "AAPL", "199.5", Currency.USD)
    provider.usd_krw = Decimal("1400")  # +7.7%: 허용 범위(10%) 안
    [event] = check_twice(broker)
    assert event.trade.fx_rate == Decimal("1400.00")
    assert event.trade.amount_krw == Decimal("560000")  # 지정가 200 * 1400 * 2


def test_buy_is_rejected_at_fill_when_cash_ran_out(broker, provider):
    broker.place_limit_order(Side.BUY, "005930", 100, Decimal("70000"))  # 7,000,000 — 지금은 가능
    broker.buy("005930", 50, expected_price=Decimal("70000"))            # 3,500,000 써버림
    [event] = check_twice(broker)
    assert event.trade is None and "현금이 부족" in event.note
    assert broker.repo.orders(open_only=True) == []
    assert broker.repo.orders()[0].status is OrderStatus.REJECTED


# ---------------------------------------------------------------- Mock_Sequrity 재현 3건
def bad_tick(provider, symbol, price, previous, currency=Currency.USD):
    provider.quotes[symbol] = Quote(symbol, symbol, Decimal(price), currency, Decimal(previous))


def test_bad_low_tick_cannot_buy_below_limit(broker, provider):
    """재현 1: 지정가 95 매수, 오류 시세 35 → 35에 체결되던 문제. 이제 한 번이면 체결 안 되고, 계속돼도 95에."""
    bad_tick(provider, "XYZ", "100", "100")
    broker.place_limit_order(Side.BUY, "XYZ", 10, Decimal("95"))
    bad_tick(provider, "XYZ", "35", "100")
    assert broker.check_limit_orders() == []          # 오류 한 번으로는 체결 없음
    bad_tick(provider, "XYZ", "100", "100")
    assert broker.check_limit_orders() == []          # 정상으로 돌아오면 확인 표시 해제
    assert broker.repo.trades() == []
    bad_tick(provider, "XYZ", "35", "100")
    [event] = check_twice(broker)                     # 오류가 두 번 이어져도
    assert event.trade.price == Decimal("95")         # 사용자가 정한 가격으로만 체결 → 공짜 이익 없음


def test_bad_high_tick_cannot_sell_above_limit(broker, provider):
    """재현 2: 지정가 105 매도, 오류 시세 290 → 290에 체결되던 문제."""
    bad_tick(provider, "XYZ", "100", "100")
    broker.buy("XYZ", 10, expected_price=Decimal("100"), expected_fx=Decimal("1300"))
    broker.place_limit_order(Side.SELL, "XYZ", 10, Decimal("105"))
    bad_tick(provider, "XYZ", "290", "100")
    [event] = check_twice(broker)
    assert event.trade.price == Decimal("105")
    assert event.trade.amount_krw == Decimal("1365000")  # 105 * 1300 * 10, 290이었다면 3,770,000


def test_bad_fx_does_not_fill(broker, provider):
    """재현 3(LO2): 1400에 산 뒤 지정가 매도, 체결 때 환율 2900 → 부풀려진 입금. 이제 체결을 미룬다."""
    bad_tick(provider, "XYZ", "100", "100")
    provider.usd_krw = Decimal("1400")
    broker.buy("XYZ", 10, expected_price=Decimal("100"), expected_fx=Decimal("1400"))
    broker.place_limit_order(Side.SELL, "XYZ", 10, Decimal("101"))   # 주문 환율 1400
    bad_tick(provider, "XYZ", "102", "100")
    provider.usd_krw = Decimal("2900")
    assert broker.check_limit_orders() == []
    assert broker.check_limit_orders() == []
    assert len(broker.repo.trades()) == 1 and broker.repo.orders(open_only=True)
    provider.usd_krw = Decimal("1420")                               # 정상 범위로 돌아오면 체결
    [event] = broker.check_limit_orders()
    assert event.trade.fx_rate == Decimal("1420.00")


def test_screening_uses_one_batch_and_rechecks_only_triggered(broker, provider):
    """Mock_QA L1 / Mock_Sequrity LO3: 조건에 안 맞는 종목은 개별 조회하지 않는다."""
    batch_calls, single_calls = [], []
    original_get_quote = provider.get_quote

    def get_quotes(items):
        batch_calls.append([s for s, _, _ in items])
        return {s: original_get_quote(s) for s, _, _ in items}

    def get_quote(symbol):
        single_calls.append(symbol)
        return original_get_quote(symbol)

    broker.place_limit_order(Side.BUY, "005930", 1, Decimal("60000"))   # 안 맞음
    broker.place_limit_order(Side.BUY, "AAPL", 1, Decimal("201"))       # 맞음(200.50 ≤ 201)
    provider.get_quotes = get_quotes
    provider.get_quote = get_quote
    broker.check_limit_orders()
    assert len(batch_calls) == 1 and sorted(batch_calls[0]) == ["005930.KS", "AAPL"]
    assert single_calls == ["AAPL"]


def test_place_rejects_obviously_unaffordable_buy(broker):
    with pytest.raises(TradingError, match="현금이 부족"):
        broker.place_limit_order(Side.BUY, "005930", 1000, Decimal("70000"))


def test_sell_orders_cannot_exceed_holdings_including_pending(broker):
    broker.buy("005930", 5, expected_price=Decimal("70000"))
    broker.place_limit_order(Side.SELL, "005930", 3, Decimal("80000"))
    with pytest.raises(TradingError, match="보유 수량"):
        broker.place_limit_order(Side.SELL, "005930", 3, Decimal("80000"))  # 3 + 3 > 5
    broker.place_limit_order(Side.SELL, "005930", 2, Decimal("80000"))


@pytest.mark.parametrize("limit", ["34999", "105001", "0", "-1", "NaN", "abc"])
def test_limit_price_must_be_sane(broker, limit):
    with pytest.raises(TradingError):
        broker.place_limit_order(Side.BUY, "005930", 1, limit)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True])
def test_quantity_validated(broker, quantity):
    with pytest.raises(TradingError):
        broker.place_limit_order(Side.BUY, "005930", quantity, Decimal("70000"))


def test_cancel_only_open_orders(broker):
    order = broker.place_limit_order(Side.BUY, "005930", 1, Decimal("60000"))
    assert broker.cancel_order(order.id) is True
    assert broker.cancel_order(order.id) is False
    assert broker.repo.orders()[0].status is OrderStatus.CANCELLED
    with pytest.raises(TradingError):
        broker.cancel_order("1")


def test_order_cancelled_during_fill_leaves_no_trade(broker, provider):
    """시세를 확인하는 사이(트랜잭션 밖) 사용자가 취소하면 체결 기록을 남기지 않는다."""
    order = broker.place_limit_order(Side.BUY, "005930", 1, Decimal("70000"))
    broker.check_limit_orders()  # 확인 1회
    original_fill = broker._fill_order

    def cancelled_just_before(*args, **kwargs):
        broker.cancel_order(order.id)  # 다른 트랜잭션에서 먼저 커밋된 취소
        return original_fill(*args, **kwargs)

    broker._fill_order = cancelled_just_before
    assert broker.check_limit_orders() == []
    assert broker.repo.trades() == []
    assert broker.repo.orders()[0].status is OrderStatus.CANCELLED
    assert broker.repo.cash() == Decimal(10_000_000)


def test_reset_removes_orders(broker):
    broker.place_limit_order(Side.BUY, "005930", 1, Decimal("60000"))
    broker.open_account(5_000_000)
    assert broker.repo.orders() == []


def test_open_order_limit(broker, provider):
    for _ in range(MAX_OPEN_ORDERS):
        broker.place_limit_order(Side.BUY, "005930", 1, Decimal("60000"))
    with pytest.raises(TradingError, match=f"{MAX_OPEN_ORDERS}개"):
        broker.place_limit_order(Side.BUY, "005930", 1, Decimal("60000"))


def test_quote_failure_backs_off_then_retries(broker, provider):
    """시세를 못 받은 종목은 5분 동안 다시 묻지 않고, 그 뒤에 다시 확인한다."""
    from datetime import datetime, timedelta

    broker.place_limit_order(Side.BUY, "AAPL", 1, Decimal("200.50"))
    saved = provider.quotes.pop("AAPL")
    now = datetime(2026, 10, 15, 12, 0)
    assert broker.check_limit_orders(now) == []
    provider.quotes["AAPL"] = saved
    assert broker.check_limit_orders(now + timedelta(minutes=1)) == []   # 아직 쉬는 중 → 확인 표시도 없음
    assert broker.repo.orders(open_only=True)[0].armed is False
    later = now + timedelta(minutes=5)
    assert len(check_twice(broker, later)) == 1


def test_orders_fill_oldest_first(broker, provider):
    first = broker.place_limit_order(Side.BUY, "005930", 100, Decimal("70000"))
    broker.place_limit_order(Side.BUY, "005930", 100, Decimal("70000"))  # 둘 다는 못 삼
    events = check_twice(broker)
    assert events[0].order.id == first.id and events[0].trade is not None
    assert events[1].trade is None


def test_sell_error_message_mentions_pending(broker):
    """Mock_QA L3"""
    broker.buy("005930", 5, expected_price=Decimal("70000"))
    broker.place_limit_order(Side.SELL, "005930", 3, Decimal("80000"))
    with pytest.raises(TradingError, match="주문 3주 \\+ 대기 중인 판매 3주 / 보유 5주"):
        broker.place_limit_order(Side.SELL, "005930", 3, Decimal("80000"))
    assert broker.pending_sell_quantity("005930.KS") == 3


def test_window_limit_order_flow(provider):
    """화면: 지정가로 걸어 두기 → 가격이 내려오면 자동 확인에서 체결 → 주문 표와 상태 줄에 반영."""
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from mock_trading.ui import main_window as mw

    app = QApplication.instance() or QApplication([])
    broker = Broker(Repository(check_same_thread=False), provider)
    broker.open_account(10_000_000)
    window = mw.MainWindow(broker)
    original_confirm = mw._confirm
    mw._confirm = lambda *a, **k: True

    def settle():
        for _ in range(3):
            window.worker.wait(3000)
            deadline = time.time() + 0.3
            while time.time() < deadline:
                app.processEvents()

    try:
        window.load_quote("005930")
        settle()
        window.order_type.setCurrentIndex(1)
        assert window.limit_price.isEnabled() and window.limit_price.value() == 70000
        window.limit_price.setValue(65000)
        window.quantity.setValue(3)
        assert "지정가 기준" in window.estimate_label.text() and "195,029원" in window.estimate_label.text()  # 195,000 + 수수료 0.015%
        window.place_order(Side.BUY)
        settle()
        assert window.orders_table.rowCount() == 1 and window.orders_table.item(0, 6).text() == "대기"

        set_price(provider, "005930.KS", "64000")
        window.refresh_all()   # 확인 1회
        settle()
        assert window.orders_table.item(0, 6).text() == "대기 (확인 1회)"
        window.refresh_all()   # 두 번째에 체결
        settle()
        assert window.orders_table.item(0, 6).text() == "체결"
        assert len(broker.repo.trades()) == 1 and broker.repo.trades()[0].price == Decimal("65000")
        assert window.tabs.tabText(window.tabs.indexOf(window.orders_tab)) == "지정가 주문 (1)"  # Mock_QA L2
        assert window._order_notice is not None and "체결" in window._order_notice.text()
        window.tabs.setCurrentWidget(window.orders_tab)
        assert window.tabs.tabText(window.tabs.indexOf(window.orders_tab)) == "지정가 주문"
    finally:
        mw._confirm = original_confirm
        window.close()
