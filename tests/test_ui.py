"""화면 동작 테스트. 창을 띄우지 않는 offscreen 플랫폼에서 돌린다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mock_trading.broker import Broker  # noqa: E402
from mock_trading.storage import Repository  # noqa: E402
from mock_trading.ui.main_window import MainWindow, _QuoteView  # noqa: E402

from .test_broker import FakeProvider  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    broker = Broker(Repository(check_same_thread=False), FakeProvider())
    broker.open_account(10_000_000)
    w = MainWindow(broker)
    yield w
    w.worker.wait(3000)
    w.close()


def _view(window, held=0):
    quote = window.broker.provider.get_quote("005930.KS")
    return _QuoteView(quote, [], window.broker.max_buy_quantity(quote), held, Decimal(1))


def test_quote_enables_buy_and_sell(window):
    window._show_quote(_view(window, held=3))
    assert window.buy_button.isEnabled()
    assert window.sell_button.isEnabled()


def test_quote_arriving_during_reset_does_not_enable_orders(window):
    """QA N1·F2: 초기화가 대기 중이면 늦게 도착한 시세 결과도 주문 버튼을 켜지 못한다."""
    window._reset_pending = True
    window._show_quote(_view(window, held=3))
    assert not window.buy_button.isEnabled()
    assert not window.sell_button.isEnabled()


def test_quote_arriving_during_order_does_not_enable_orders(window):
    """중복 주문 방지(QA U3)"""
    window._order_pending = True
    window._show_quote(_view(window, held=3))
    assert not window.buy_button.isEnabled()


def test_sell_disabled_without_holdings(window):
    window._show_quote(_view(window, held=0))
    assert window.buy_button.isEnabled()
    assert not window.sell_button.isEnabled()
