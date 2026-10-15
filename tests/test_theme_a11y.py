"""테마를 입힌 상태의 키보드 접근성 (Mock_QA A1·A2·A6).

테스트는 보통 테마 없이 돌기 때문에, 테마가 만든 문제는 여기서 테마를 입혀 확인한다.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from mock_trading.broker import Broker  # noqa: E402
from mock_trading.domain import OrderStatus, Side  # noqa: E402
from mock_trading.storage import Repository  # noqa: E402
from mock_trading.ui.main_window import MainWindow  # noqa: E402
from mock_trading.ui.theme import apply_theme  # noqa: E402

from .test_broker import FakeProvider  # noqa: E402


@pytest.fixture
def themed_app():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    yield app
    app.setStyleSheet("")


def _settle(app, window=None):
    if window is not None:
        window.worker.wait(3000)
    deadline = time.time() + 0.3
    while time.time() < deadline:
        app.processEvents()


def _focus_changes_look(app, widget, holder):
    """포커스가 없을 때와 있을 때 화면이 달라야 키보드 사용자가 위치를 안다."""
    holder.show()
    app.processEvents()
    widget.clearFocus()
    app.processEvents()
    before = widget.grab().toImage()
    widget.setFocus(Qt.TabFocusReason)
    app.processEvents()
    assert widget.hasFocus()
    after = widget.grab().toImage()
    return before != after


def test_buttons_show_keyboard_focus(themed_app):
    holder = QWidget()
    plain = QPushButton("새로고침", holder)
    other = QPushButton("다른 버튼", holder)
    other.move(0, 60)
    assert _focus_changes_look(themed_app, plain, holder)
    holder.close()


def test_order_buttons_and_tables_show_focus(themed_app):
    broker = Broker(Repository(check_same_thread=False), FakeProvider())
    broker.open_account(10_000_000)
    window = MainWindow(broker)
    window.show()
    window.load_quote("005930")
    _settle(themed_app, window)
    assert window.buy_button.isEnabled()
    assert _focus_changes_look(themed_app, window.buy_button, window)
    assert window.holdings_table.focusPolicy() != Qt.NoFocus  # A2: 표에 다시 들어갈 수 있다
    assert _focus_changes_look(themed_app, window.holdings_table, window)
    window.close()


def test_delete_key_cancels_selected_order_and_enter_opens_stock(themed_app):
    provider = FakeProvider()
    broker = Broker(Repository(check_same_thread=False), provider)
    broker.open_account(10_000_000)
    broker.buy("005930", 1, expected_price=Decimal("70000"))
    broker.place_limit_order(Side.BUY, "005930", 1, Decimal("60000"), expected_price=Decimal("70000"))
    window = MainWindow(broker)
    window.show()
    window.refresh_all()
    _settle(themed_app, window)

    # Delete로 선택한 대기 주문 취소
    window.tabs.setCurrentWidget(window.orders_tab)
    window.orders_table.setFocus()
    window.orders_table.selectRow(0)
    QTest.keyClick(window.orders_table, Qt.Key_Delete)  # 실제 키 입력
    _settle(themed_app, window)
    assert broker.repo.orders()[0].status is OrderStatus.CANCELLED

    # Enter(itemActivated)로 보유 종목 열기
    window.tabs.setCurrentIndex(0)
    item = window.holdings_table.item(0, 0)
    window.holdings_table.itemActivated.emit(item)
    _settle(themed_app, window)
    assert window.current is not None and window.current.quote.symbol == "005930.KS"
    window.close()
