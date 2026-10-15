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


def test_sidebar_switches_pages_and_mirrors_badge(window):
    """왼쪽 메뉴: 누르면 페이지가 바뀌고, 선택 표시와 지정가 건수 배지가 메뉴에도 보인다."""
    labels = [b.text() for b in window.nav_buttons]
    assert labels == ["내 계좌", "주식 주문", "거래 내역", "관심 종목", "자산 추이", "지정가 주문"]
    assert window.tabs.tabBar().isHidden()
    window.nav_buttons[1].click()
    assert window.tabs.currentWidget() is window.order_page
    assert window.nav_buttons[1].isChecked() and not window.nav_buttons[0].isChecked()

    window.tabs.setTabText(window.tabs.indexOf(window.orders_tab), "지정가 주문 (2)")
    window._sync_nav()
    assert window.nav_buttons[5].text() == "지정가 주문 (2)"
    window.nav_buttons[5].click()
    assert window.nav_buttons[5].text() == "지정가 주문"  # 열면 배지가 지워진다


def test_opening_a_holding_goes_to_order_page(window):
    from PySide6.QtWidgets import QTableWidgetItem

    window.tabs.setCurrentWidget(window.account_page)
    window.holdings_table.setRowCount(1)
    window.holdings_table.setItem(0, 0, QTableWidgetItem("Samsung"))
    window.holdings_table.setItem(0, 1, QTableWidgetItem("005930.KS"))
    window._open_holding(0, 0)
    assert window.tabs.currentWidget() is window.order_page
    assert window.symbol_input.text() == "005930.KS"


def test_focus_follows_selected_menu(window, app):
    window.show()
    window.nav_buttons[0].setFocus()
    app.processEvents()
    window.tabs.setCurrentWidget(window.order_page)  # 프로그램이 페이지를 바꿈
    app.processEvents()
    assert window.nav_buttons[1].hasFocus() and not window.nav_buttons[0].hasFocus()


def test_cancel_confirmation_counts_only_open_orders(window, app):
    """Mock_QA: 확인창의 "N건"은 선택한 행 중 대기 중인 주문만."""
    from PySide6.QtWidgets import QTableWidgetItem

    from mock_trading.ui import main_window as mw

    window.orders_table.setRowCount(3)
    for row, (order_id, status) in enumerate([(3, "대기"), (2, "체결"), (1, "대기 (확인 1회)")]):
        window.orders_table.setItem(row, 0, QTableWidgetItem(str(order_id)))
        window.orders_table.setItem(row, 6, QTableWidgetItem(status))
    window.orders_table.selectAll()
    asked = []
    original = mw._confirm
    mw._confirm = lambda parent, title, text: asked.append(text) or False
    try:
        window._cancel_selected_orders()
        assert asked == ["선택한 대기 주문 2건을 취소할까요?"]
        window.orders_table.clearSelection()
        window.orders_table.selectRow(1)  # 체결된 행만
        window._cancel_selected_orders()
        assert len(asked) == 1 and "대기 중인 주문이 없습니다" in window.status.text()
    finally:
        mw._confirm = original
