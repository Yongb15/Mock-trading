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


def test_buy_waits_until_price_falls_to_limit_and_fills_at_current_price(broker, provider):
    order = broker.place_limit_order(Side.BUY, "005930", 10, Decimal("65000"))  # 현재 70,000
    assert order.status is OrderStatus.OPEN and order.symbol == "005930.KS"
    assert broker.check_limit_orders() == []
    set_price(provider, "005930.KS", "64000")  # 지정가보다 더 싸게 → 그 가격으로 체결
    [event] = broker.check_limit_orders()
    assert event.trade.price == Decimal("64000") and event.trade.side is Side.BUY
    [stored] = broker.repo.orders()
    assert stored.status is OrderStatus.FILLED and stored.trade_id == event.trade.id
    assert broker.repo.cash() == Decimal(10_000_000) - Decimal(640_000) - Decimal(640)


def test_sell_fills_when_price_rises_to_limit(broker, provider):
    broker.buy("005930", 5, expected_price=Decimal("70000"))
    broker.place_limit_order(Side.SELL, "005930", 5, Decimal("75000"))
    set_price(provider, "005930.KS", "74999")
    assert broker.check_limit_orders() == []
    set_price(provider, "005930.KS", "76000")
    [event] = broker.check_limit_orders()
    assert event.trade.side is Side.SELL and event.trade.price == Decimal("76000")
    assert broker.repo.holdings() == {}


def test_filled_order_is_not_filled_twice(broker, provider):
    broker.place_limit_order(Side.BUY, "005930", 1, Decimal("70000"))
    broker.check_limit_orders()
    assert broker.check_limit_orders() == []
    assert len(broker.repo.trades()) == 1


def test_us_limit_order_uses_fx_at_fill(broker, provider):
    broker.place_limit_order(Side.BUY, "AAPL", 2, Decimal("200"))  # 현재 200.50
    set_price(provider, "AAPL", "199.5", Currency.USD)
    provider.usd_krw = Decimal("1400")
    [event] = broker.check_limit_orders()
    assert event.trade.fx_rate == Decimal("1400.00")
    assert event.trade.amount_krw == Decimal("558600")  # 199.5 * 1400 * 2


def test_buy_is_rejected_at_fill_when_cash_ran_out(broker, provider):
    broker.place_limit_order(Side.BUY, "005930", 100, Decimal("70000"))  # 7,000,000 — 지금은 가능
    broker.buy("005930", 50, expected_price=Decimal("70000"))            # 3,500,000 써버림
    [event] = broker.check_limit_orders()
    assert event.trade is None and "현금이 부족" in event.note
    open_orders = broker.repo.orders(open_only=True)
    assert open_orders == []
    assert broker.repo.orders()[0].status is OrderStatus.REJECTED


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


def test_quote_failure_skips_order_until_next_check(broker, provider):
    broker.place_limit_order(Side.BUY, "AAPL", 1, Decimal("200.50"))
    saved = provider.quotes.pop("AAPL")
    assert broker.check_limit_orders() == []
    provider.quotes["AAPL"] = saved
    assert len(broker.check_limit_orders()) == 1


def test_orders_fill_oldest_first(broker, provider):
    first = broker.place_limit_order(Side.BUY, "005930", 100, Decimal("70000"))
    broker.place_limit_order(Side.BUY, "005930", 100, Decimal("70000"))  # 둘 다는 못 삼
    events = broker.check_limit_orders()
    assert events[0].order.id == first.id and events[0].trade is not None
    assert events[1].trade is None


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
        window.refresh_all()
        settle()
        assert window.orders_table.item(0, 6).text() == "체결"
        assert "지정가 매수 체결" in window.status.text() or broker.repo.trades()
        assert len(broker.repo.trades()) == 1 and broker.repo.trades()[0].price == Decimal("64000")
    finally:
        mw._confirm = original_confirm
        window.close()
