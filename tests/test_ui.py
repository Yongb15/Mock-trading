"""화면 동작 테스트. 창을 띄우지 않는 offscreen 플랫폼에서 돌린다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402

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
    assert labels == ["내 계좌", "주식 주문", "시장 순위", "거래 내역", "관심 종목", "자산 추이", "지정가 주문"]
    orders = window.tabs.indexOf(window.orders_tab)
    assert window.tabs.tabBar().isHidden()
    window.nav_buttons[1].click()
    assert window.tabs.currentWidget() is window.order_page
    assert window.nav_buttons[1].isChecked() and not window.nav_buttons[0].isChecked()

    window.tabs.setTabText(window.tabs.indexOf(window.orders_tab), "지정가 주문 (2)")
    window._sync_nav()
    assert window.nav_buttons[orders].text() == "지정가 주문 (2)"
    window.nav_buttons[orders].click()
    assert window.nav_buttons[orders].text() == "지정가 주문"  # 열면 배지가 지워진다


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

    from mock_trading.domain import OrderStatus

    window.orders_table.setRowCount(3)
    rows = [(3, "기다리는 중", OrderStatus.OPEN), (2, "체결", OrderStatus.FILLED), (1, "대기 (확인 1회)", OrderStatus.OPEN)]
    for row, (order_id, text, status) in enumerate(rows):
        window.orders_table.setItem(row, 0, QTableWidgetItem(str(order_id)))
        # 문구가 "대기"로 시작하지 않아도(Mock_QA Z3) 담아 둔 상태 값으로 판단한다
        window.orders_table.setItem(row, 6, window._status_item(text, "", status))
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


def test_tab_leaves_the_menu(window, app):
    """Mock_QA N1: 선택된 메뉴에서 Tab을 누르면 메뉴가 아닌 위젯으로 간다(키보드 트랩 방지)."""
    from PySide6.QtTest import QTest

    window.show()
    for page in (0, 1):
        window.nav_buttons[page].click()
        window.nav_buttons[page].setFocus()
        app.processEvents()
        QTest.keyClick(app.focusWidget(), Qt.Key_Tab)
        app.processEvents()
        assert app.focusWidget() not in window.nav_buttons


def test_window_fits_a_small_laptop_screen(window, app):
    """Mock_QA N2: 시세를 불러온 뒤에도 창 최소 폭이 1,000px를 넘지 않는다."""
    window.load_quote("005930")
    window.worker.wait(3000)
    app.processEvents()
    window.tabs.setCurrentWidget(window.order_page)
    app.processEvents()
    assert window.minimumSizeHint().width() <= 1000


def test_time_and_name_columns_are_not_cut_off(window):
    """README 화면 확인 중 발견: 주문 시각·종목명이 '2026-10-16 ...'처럼 잘리지 않게 내용 길이에 맞춘다."""
    from PySide6.QtWidgets import QHeaderView

    for table, columns in [(window.orders_table, (1, 3)), (window.trades_table, (0, 2))]:
        header = table.horizontalHeader()
        for column in range(table.columnCount()):
            expected = QHeaderView.Interactive if column in columns else QHeaderView.Stretch
            assert header.sectionResizeMode(column) == expected


def test_long_name_is_capped_with_tooltip(window):
    """Mock_QA RM4: 긴 종목명 하나가 다른 열을 최소 폭으로 누르지 않게 상한을 두고 전체 이름은 툴팁으로."""
    from PySide6.QtWidgets import QTableWidgetItem

    from mock_trading.ui.main_window import FIT_COLUMN_MAX

    table = window.trades_table
    table.setRowCount(1)
    short, long = "2026-10-16 10:00:00", "SPDR S&P 500 ETF Trust Long Name " * 3
    table.setItem(0, 0, QTableWidgetItem(short))
    table.setItem(0, 2, QTableWidgetItem(long))
    window._fit_columns(table)
    assert table.columnWidth(2) == FIT_COLUMN_MAX
    assert table.columnWidth(0) < FIT_COLUMN_MAX  # 짧은 열은 내용 폭 그대로
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setHtml(table.item(0, 2).toolTip())
    assert doc.toPlainText() == long


def test_tooltip_does_not_render_html_from_names(window):
    """Mock_Sequrity RM-T1: 종목명에 태그가 있어도 툴팁에서 서식·이미지로 해석하지 않고 글자 그대로 보인다."""
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QTableWidgetItem

    table = window.orders_table
    table.setRowCount(1)
    name = '<b>Evil</b> <img src="file:///C:/x.png"> Corp'
    table.setItem(0, 3, QTableWidgetItem(name))
    window._fit_columns(table)
    doc = QTextDocument()
    doc.setHtml(table.item(0, 3).toolTip())
    assert doc.toPlainText() == name


def test_window_fits_with_large_account_numbers(window, app):
    """Mock_QA RM3: 요약 칸에 10자리 금액이 들어가도 창 최소 폭이 1,000px 이하.
    요약이 '-'일 때나 테마(큰 글씨) 없이 재면 놓치므로 실제 테마를 입히고 가장 긴 값을 넣어 잰다."""
    from mock_trading.ui.theme import apply_theme

    font = app.font()
    apply_theme(app)
    try:
        widest = {"total": "1,999,999,999원", "cash": "1,999,999,999원", "stock": "1,999,999,999원",
                  "return": "-99.99%", "realized": "-1,999,999,999원"}
        for key, text in widest.items():
            window.summary_labels[key].setText(text)
        window.tabs.setCurrentWidget(window.account_page)
        app.processEvents()
        assert window.minimumSizeHint().width() <= 1000
    finally:
        app.setStyleSheet("")
        app.setFont(font)


def test_summary_numbers_shrink_together_when_narrow(app):
    """좁은 칸에서는 글자를 줄여 다 보이게 하고, 같은 줄 라벨은 같은 크기로 줄어든다."""
    from PySide6.QtGui import QFont

    from mock_trading.ui.main_window import FitLabel

    font = QFont()
    font.setPixelSize(22)
    long, short = FitLabel("-1,999,999,999원", min_width=80), FitLabel("-1.00%", min_width=80)
    for label in (long, short):
        label.setFont(font)
        label.peers = [long, short]
        label.resize(400, 40)
    assert long.fitted_font().pixelSize() == 22  # 넉넉하면 그대로
    for label in (long, short):
        label.resize(80, 40)
    shrunk = long.fitted_font().pixelSize()
    assert shrunk < 22 and short.fitted_font().pixelSize() == shrunk
    assert shrunk >= FitLabel.MIN_PIXEL  # 읽을 수 있는 크기 아래로는 줄이지 않는다
    assert long.minimumSizeHint().width() == 80  # 글자 길이가 최소 폭을 정하지 않는다


def test_summary_number_elides_front_instead_of_cutting_off(app):
    """Mock_QA S1: 최소 글자 크기로도 넘치면 끝자리를 자르지 않고 앞쪽을 …로 생략, 전체는 툴팁으로."""
    from PySide6.QtGui import QFont, QFontMetrics, QTextDocument

    from mock_trading.ui.main_window import FitLabel

    font = QFont()
    font.setPixelSize(22)
    label = FitLabel("-1,999,999,999원", min_width=40)
    label.setFont(font)
    label.resize(60, 40)
    label.show()
    app.processEvents()
    fitted = label.fitted_font()
    assert fitted.pixelSize() == FitLabel.MIN_PIXEL
    shown = label.shown_text(fitted)
    assert shown.startswith("-…") and shown.endswith("원")  # 부호는 남기고 앞자리만 생략(E1)
    assert QFontMetrics(fitted).horizontalAdvance(shown) <= label.contentsRect().width()
    doc = QTextDocument()
    doc.setHtml(label.toolTip())
    assert doc.toPlainText() == "-1,999,999,999원"
    label.resize(400, 40)
    app.processEvents()
    assert label.toolTip() == ""
    label.close()


def test_table_headers_have_tooltips(window):
    """좁은 창에서 머리글이 잘려도 마우스를 올리면 전체 이름이 보인다."""
    for table in (window.holdings_table, window.trades_table, window.orders_table, window.rank_table):
        for column in range(table.columnCount()):
            item = table.horizontalHeaderItem(column)
            assert item.toolTip() == item.text()


def test_elided_number_keeps_its_sign(app):
    """Mock_QA E1: 앞자리를 생략해도 +/- 부호는 남는다(지우면 손실이 이익처럼 보인다)."""
    from PySide6.QtGui import QFont, QFontMetrics

    from mock_trading.ui.main_window import FitLabel

    font = QFont()
    font.setPixelSize(22)
    for text in ["-1,999,999,999원", "+1,234,567,890원", "-12.34%"]:
        label = FitLabel(text, min_width=40)
        label.setFont(font)
        label.resize(60, 40)
        fitted = label.fitted_font()
        shown = label.shown_text(fitted)
        assert shown[0] == text[0] and "…" in shown
        assert QFontMetrics(fitted).horizontalAdvance(shown) <= label.contentsRect().width()
    plain = FitLabel("1,999,999,999원", min_width=40)
    plain.setFont(font)
    plain.resize(60, 40)
    assert plain.shown_text(plain.fitted_font()).startswith("…")


def test_search_by_name_loads_the_quote(window, app):
    """이름을 치면 목록이 뜨고, 고르거나 이름 그대로 Enter를 치면 그 종목을 조회한다."""
    window._suggest_symbols("삼성전")
    assert window.symbol_matches.stringList()[0] == "삼성전자  005930.KS"
    window._pick_symbol("삼성전자  005930.KS")
    window.worker.wait(3000)
    app.processEvents()
    assert window.symbol_input.text() == "005930.KS"
    assert window.current and window.current.quote.symbol == "005930.KS"

    window.current = None
    window.symbol_input.setText("삼성전자")
    window.load_quote()
    window.worker.wait(3000)
    app.processEvents()
    assert window.current and window.current.quote.symbol == "005930.KS"
