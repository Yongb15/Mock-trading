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


def test_korean_composition_is_searched_while_typing(window, app):
    """조합 중인 한글(아직 text()에 없음)도 붙여서 찾는다: "삼"이 입력된 상태에서 "성"을 조합 중."""
    from PySide6.QtGui import QInputMethodEvent

    window.symbol_input.setText("삼")
    window.symbol_input.setCursorPosition(1)
    app.sendEvent(window.symbol_input, QInputMethodEvent("성", []))
    assert window.symbol_matches.stringList()[0].startswith("삼성")


def test_choosing_from_the_popup_with_keyboard(window, app):
    """목록에서 방향키 + Enter로 고르면 그 종목을 조회한다(마우스 없이)."""
    from PySide6.QtTest import QTest

    window.show()
    window.tabs.setCurrentWidget(window.order_page)
    window.symbol_input.setFocus()
    window.symbol_input.setText("삼성전")
    window._suggest_symbols("삼성전")
    popup = window.symbol_completer.popup()
    app.processEvents()
    assert popup.isVisible()
    calls = []
    original = window.load_quote
    window.load_quote = lambda symbol=None: (calls.append(symbol), original(symbol))
    QTest.keyClick(popup, Qt.Key_Down)
    QTest.keyClick(popup, Qt.Key_Return)
    window.worker.wait(3000)
    app.processEvents()
    assert window.current and window.current.quote.symbol == "005930.KS"
    assert len(calls) == 1  # Mock_QA NS1: Enter가 입력창에도 가서 두 번 조회하지 않는다


def test_partial_name_enter_picks_single_match_or_asks(window, app):
    """Mock_QA NS2: 이름 일부 + Enter — 후보가 하나면 그 종목, 여럿이면 조회하지 않고 고르라고 안내."""
    window.current = None
    window.symbol_input.setText("하이닉스")
    window.load_quote()
    window.worker.wait(3000)
    app.processEvents()
    assert window.current is None or window.current.quote.symbol == "000660.KS"
    queued = []
    window.worker.run = lambda *a, **k: queued.append(a)
    window.symbol_input.setText("삼성")
    window.load_quote()
    assert queued == [] and "골라 주세요" in window.status.text()
    window.symbol_input.setText("하이닉스")
    window.load_quote()
    assert len(queued) == 1  # 하나뿐이면 바로 조회


def test_enter_without_choosing_still_looks_up_once(window, app):
    """목록이 떠 있어도 고르지 않고 Enter → 입력한 그대로 한 번 조회(조회가 사라지거나 두 번 되지 않음)."""
    from PySide6.QtTest import QTest

    window.show()
    window.tabs.setCurrentWidget(window.order_page)
    window.symbol_input.setFocus()
    window.symbol_input.setText("005930")
    window._suggest_symbols("005930")
    popup = window.symbol_completer.popup()
    app.processEvents()
    assert popup.isVisible()
    calls = []
    original = window.load_quote
    window.load_quote = lambda symbol=None: (calls.append(symbol), original(symbol))
    QTest.keyClick(popup, Qt.Key_Return)
    window.worker.wait(3000)
    app.processEvents()
    assert len(calls) == 1
    assert window.current and window.current.quote.symbol == "005930.KS"


def test_enter_keeps_typed_code_when_nothing_chosen(window, app):
    """목록 첫 항목을 미리 고르지 않는다: "AAP"(실제 있는 티커) + Enter가 AAPL로 바뀌지 않는다."""
    from PySide6.QtTest import QTest

    window.show()
    window.tabs.setCurrentWidget(window.order_page)
    window.symbol_input.setFocus()
    window.symbol_input.setText("AAP")
    window._suggest_symbols("AAP")
    popup = window.symbol_completer.popup()
    app.processEvents()
    assert popup.isVisible() and not popup.currentIndex().isValid()
    calls = []
    original = window.load_quote
    window.load_quote = lambda symbol=None: (calls.append(symbol), original(symbol))
    QTest.keyClick(popup, Qt.Key_Return)
    window.worker.wait(3000)
    app.processEvents()
    assert calls == [None] and window.symbol_input.text() == "AAP" and not popup.isVisible()


def test_lookup_button_behaves_like_enter(window, app):
    """Mock_QA NS6: "조회" 버튼도 Enter와 같다 — 여러 후보인 이름 일부는 조회하지 않고 고르라고 안내."""
    from PySide6.QtWidgets import QPushButton

    lookup = next(b for b in window.findChildren(QPushButton) if b.text() == "조회")
    queued = []
    window.worker.run = lambda *a, **k: queued.append(a)
    window.symbol_input.setText("삼성")
    lookup.click()
    assert queued == [] and "골라 주세요" in window.status.text()
    window.symbol_input.setText("005930")
    lookup.click()
    assert len(queued) == 1
def test_chart_range_reloads_only_history(window, app):
    """기간 버튼은 차트만 그 기간으로 다시 받는다. 제목·축 형식이 기간을 따른다."""
    from datetime import datetime, timedelta
    from decimal import Decimal

    from mock_trading.market_data import HISTORY_PERIODS, PricePoint
    from mock_trading.ui.main_window import CHART_RANGES

    assert {period for period, _ in CHART_RANGES} <= HISTORY_PERIODS  # 공급자가 허용하는 기간만
    asked = []
    start = datetime(2025, 1, 2)

    def history(symbol, period="3mo"):
        asked.append((symbol, period))
        return [PricePoint(start + timedelta(days=i), Decimal(100 + i)) for i in range(5)]

    window.broker.provider.get_history = history
    window.load_quote("005930")
    window.worker.wait(3000)
    app.processEvents()
    assert asked[-1] == ("005930.KS", "3mo")
    year = next(b for b in window.chart_range_group.buttons() if b.property("value") == "1y")
    year.click()
    window.reader.wait(3000)
    app.processEvents()
    assert asked[-1] == ("005930.KS", "1y")
    assert "최근 1년" in window.chart.title()
    assert window.chart.axes(Qt.Horizontal)[0].format() == "yy/MM"
    window.load_quote("005930")  # 자동 갱신도 고른 기간을 유지
    window.worker.wait(3000)
    app.processEvents()
    assert asked[-1] == ("005930.KS", "1y")


def test_late_chart_result_for_old_range_is_ignored(window, app):
    """기간을 빠르게 바꾸면 늦게 온 이전 기간 결과로 차트를 덮어쓰지 않는다."""
    from datetime import datetime
    from decimal import Decimal

    from mock_trading.market_data import PricePoint

    window.load_quote("005930")
    window.worker.wait(3000)
    app.processEvents()
    jobs = []
    window.reader.run = lambda fn, done, failed: jobs.append((fn, done))
    buttons = {b.property("value"): b for b in window.chart_range_group.buttons()}
    buttons["5y"].click()
    buttons["1mo"].click()
    old_done = jobs[0][1]
    old_done([PricePoint(datetime(2020, 1, 1), Decimal(1))])  # 5년 결과가 늦게 도착
    assert "최근 5년" not in window.chart.title()


def _hold_jobs(window):
    """작업을 바로 실행하지 않고 붙잡아 둔다(도착 순서를 테스트가 정한다)."""
    held = {"worker": [], "reader": []}
    window.worker.run = lambda fn, done=None, failed=None: held["worker"].append((fn, done, failed))
    window.reader.run = lambda fn, done=None, failed=None: held["reader"].append((fn, done, failed))
    return held


def _finish(job):
    fn, done, failed = job
    try:
        result = fn()
    except Exception as error:  # noqa: BLE001
        failed(str(error))
    else:
        done(result)


def _history_provider(window, asked):
    from datetime import datetime, timedelta
    from decimal import Decimal

    from mock_trading.market_data import PricePoint

    sizes = {"1mo": 20, "3mo": 60, "1y": 250, "5y": 1250}

    def history(symbol, period="3mo"):
        asked.append(period)
        return [PricePoint(datetime(2020, 1, 1) + timedelta(days=i), Decimal(100)) for i in range(sizes[period])]

    window.broker.provider.get_history = history


def _open(window, app, symbol="005930"):
    window.load_quote(symbol)
    window.worker.wait(3000)
    app.processEvents()


def test_queued_quote_does_not_overwrite_newer_range(window, app):
    """Mock_QA CR1: 대기열의 시세 조회(3개월)가 그사이 고른 5년 차트를 덮어쓰지 않는다. 제목과 데이터가 일치."""
    asked = []
    _history_provider(window, asked)
    _open(window, app)
    held = _hold_jobs(window)
    window.load_quote("005930")  # 3개월로 다시 조회가 대기열에 있음
    buttons = {b.property("value"): b for b in window.chart_range_group.buttons()}
    buttons["5y"].click()
    _finish(held["reader"].pop())  # 5년 차트 도착
    _finish(held["worker"].pop())  # 늦게 3개월 시세 도착
    assert len(window.current.history) == 1250 and window.current.period == "5y"
    assert "최근 5년" in window.chart.title()
    assert held["reader"] == []  # 다시 받을 필요 없음


def test_auto_refresh_does_not_refetch_history(window, app):
    """Mock_Sequrity CR1: 60초 자동 갱신은 시세만 받고 이력은 화면에 있는 것을 쓴다."""
    asked = []
    _history_provider(window, asked)
    _open(window, app)
    buttons = {b.property("value"): b for b in window.chart_range_group.buttons()}
    held = _hold_jobs(window)
    buttons["5y"].click()
    _finish(held["reader"].pop())
    asked.clear()
    window.refresh_all()
    for job in [j for j in held["worker"]]:
        _finish(job)
    assert asked == [] and len(window.current.history) == 1250 and "최근 5년" in window.chart.title()


def test_range_reclick_and_rapid_clicks(window, app):
    """Mock_Sequrity CR2: 같은 기간 재클릭은 무시, 받는 중 연타는 끝난 뒤 마지막 기간 한 번만."""
    asked = []
    _history_provider(window, asked)
    _open(window, app)
    held = _hold_jobs(window)
    buttons = {b.property("value"): b for b in window.chart_range_group.buttons()}
    buttons["3mo"].click()
    assert held["reader"] == []
    buttons["1y"].click()
    buttons["5y"].click()
    buttons["1mo"].click()
    assert len(held["reader"]) == 1  # 진행 중인 1년 요청 하나뿐
    _finish(held["reader"].pop())  # 1년 결과는 버리고 마지막(1개월)을 한 번 더
    assert len(held["reader"]) == 1
    _finish(held["reader"].pop())
    assert window.current.period == "1mo" and len(window.current.history) == 20 and held["reader"] == []


def test_failed_range_reverts_button(window, app):
    """Mock_QA CR5: 차트를 못 받으면 버튼을 화면에 그려진 기간으로 되돌리고 알린다."""
    asked = []
    _history_provider(window, asked)
    _open(window, app)
    held = _hold_jobs(window)
    buttons = {b.property("value"): b for b in window.chart_range_group.buttons()}

    def broken(symbol, period="3mo"):
        raise ConnectionError("down")

    window.broker.provider.get_history = broken
    buttons["5y"].click()
    _finish(held["reader"].pop())
    assert window._chart_period == "3mo" and buttons["3mo"].isChecked() and not buttons["5y"].isChecked()
    assert "5년 차트를 받지 못했어요" in window.status.text()
    assert held["reader"] == []


def test_allocation_panel_legend_and_tooltip_are_plain_text(window, app):
    """자산 구성: 범례는 이름·비중·금액을 글자 그대로, 툴팁은 종목명을 HTML로 해석하지 않는다."""
    from decimal import Decimal

    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent, QTextDocument
    from PySide6.QtWidgets import QToolTip

    from mock_trading.allocation import Slice

    name = "<b>Evil</b> & Co"
    items = [Slice("X", name, Decimal(700), Decimal("70.0"), 0), Slice("현금", "현금", Decimal(300), Decimal("30.0"), None)]
    panel = window.allocation_panel
    panel.set_slices(items)
    assert [label.text() for label in panel.legend_labels] == [f"{name} 70.0% · 700원", "현금 30.0% · 300원"]
    assert all(label.textFormat() == Qt.PlainText for label in panel.legend_labels)
    bar = panel.bar
    bar.resize(400, 14)
    bar.show()
    app.processEvents()
    event = QMouseEvent(QMouseEvent.MouseMove, QPointF(10, 7), bar.mapToGlobal(QPointF(10, 7)),
                        Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    shown = []
    original = QToolTip.showText
    QToolTip.showText = staticmethod(lambda pos, text, widget=None: shown.append(text))
    try:
        bar.mouseMoveEvent(event)
    finally:
        QToolTip.showText = original
    doc = QTextDocument()
    doc.setHtml(shown[0])
    assert doc.toPlainText() == f"{name} 70.0% · 700원"


def test_allocation_after_portfolio_load(window, app):
    """계좌를 불러오면 자산 구성도 채워진다(처음엔 현금 100%)."""
    window.load_portfolio()
    window.worker.wait(3000)
    app.processEvents()
    assert [label.text().split(" ")[0] for label in window.allocation_panel.legend_labels] == ["현금"]


def test_long_names_in_allocation_legend_do_not_widen_window(window, app):
    """Mock_QA AL1: 범례에 긴 종목명 7칸이 있어도 창 최소 폭 ≤ 1000. 이름만 줄고 비중·금액은 보인다."""
    from decimal import Decimal

    from mock_trading.allocation import Slice
    from mock_trading.ui.theme import apply_theme

    font = app.font()
    apply_theme(app)
    try:
        long = "아주긴이름 Holdings Corporation International " * 2
        items = [Slice(f"S{i}", f"종목{i} {long}", Decimal(100), Decimal("12.5"), i) for i in range(5)]
        items += [Slice("기타", "기타 3종목", Decimal(100), Decimal("12.5"), None),
                  Slice("현금", "현금", Decimal(100), Decimal("12.5"), None)]
        window.allocation_panel.set_slices(items)
        window.tabs.setCurrentWidget(window.account_page)
        app.processEvents()
        assert window.minimumSizeHint().width() <= 1000
        window.resize(1000, 760)
        window.show()
        app.processEvents()
        label = window.allocation_panel.legend_labels[0]
        shown = label.shown_text()
        # 이름 앞부분과 비중은 항상 보이고(금액은 자리가 없으면 툴팁으로), 전체는 툴팁에
        assert shown != label.text() and shown.startswith("종목0") and "12.5%" in shown and "…" in shown
        assert label.toolTip() and label.text() == items[0].label + " 12.5% · 100원"
    finally:
        app.setStyleSheet("")
        app.setFont(font)


def test_allocation_marks_unpriced_and_tiny_shares(window, app):
    """Mock_QA AL4·AL5: 시세 없음(원가 평가)은 표시하고, 0.1% 미만은 <0.1%."""
    from decimal import Decimal

    from mock_trading.allocation import Slice

    items = [Slice("A", "알파", Decimal(999), Decimal("99.9"), 0, priced=False),
             Slice("현금", "현금", Decimal(1), Decimal("0.0"), None)]
    window.allocation_panel.set_slices(items)
    texts = [label.text() for label in window.allocation_panel.legend_labels]
    assert texts == ["알파 (시세 없음) 99.9% · 999원", "현금 <0.1% · 1원"]


def test_allocation_says_loading_before_first_portfolio(app):
    """Mock_QA AL3: 첫 계좌 로드 전에는 '없어요'가 아니라 '불러오는 중'."""
    from mock_trading.ui.allocation_bar import AllocationPanel

    panel = AllocationPanel()
    assert "불러오는 중" in panel.empty.text()
    panel.set_slices([])
    assert "없어요" in panel.empty.text()


def test_unpriced_long_name_keeps_some_name_visible(app):
    """'(시세 없음)'이 붙어 좁아도 이름 앞부분은 남는다(필요하면 금액을 먼저 뺀다 — 툴팁에 전체)."""
    from decimal import Decimal

    from mock_trading.allocation import Slice
    from mock_trading.ui.allocation_bar import LegendLabel

    item = Slice("A", "알파베타 아주긴이름 Holdings Corporation", Decimal(1_234_567), Decimal("12.5"), 0, priced=False)
    label = LegendLabel(item)
    label.resize(label.fontMetrics().horizontalAdvance("알파… (시세 없음) 12.5%") + 4, 20)
    label.show()
    app.processEvents()
    shown = label.shown_text()
    assert shown.startswith("알파") and "(시세 없음)" in shown and "12.5%" in shown
    assert label.toolTip()
    label.close()


def test_unpriced_mark_survives_the_narrowest_elide(app):
    """Mock_Sequrity Info: 가장 좁아(③ 전체 말줄임) 비중이 잘려도 '(시세 없음)'은 남는다."""
    from decimal import Decimal

    from mock_trading.allocation import Slice
    from mock_trading.ui.allocation_bar import UNPRICED_MARK, LegendLabel

    item = Slice("A", "<img src=x>Evil Corporation Holdings", Decimal(1_234_567), Decimal("12.5"), 0, priced=False)
    label = LegendLabel(item)
    for extra in (30, 0, -40):
        label.resize(label.fontMetrics().horizontalAdvance(UNPRICED_MARK) + extra, 20)
        shown = label.shown_text()
        assert "시세 없음" in shown or shown.endswith("…")
    label.resize(label.fontMetrics().horizontalAdvance(UNPRICED_MARK) + 30, 20)
    assert label.shown_text().endswith(UNPRICED_MARK)


def _history_points():
    from datetime import date, timedelta
    from decimal import Decimal

    from mock_trading.history import AssetPoint

    start = date(2026, 10, 1)
    return [AssetPoint(start + timedelta(days=i), Decimal(9_000_000), Decimal(1_000_000 + 100_000 * i), Decimal(10_000_000))
            for i in range(4)]


def test_history_compare_mode_draws_account_and_benchmarks(window, app):
    """시장과 비교: 내 계좌·코스피200·S&P500을 한 축(%)에 그리고, 기준선(0%)은 범례에서 뺀다."""
    from decimal import Decimal

    from mock_trading.history import BenchmarkSeries

    points = _history_points()
    benchmarks = [BenchmarkSeries("069500.KS", "코스피200", [(p.day, Decimal(i)) for i, p in enumerate(points)]),
                  BenchmarkSeries("SPY", "S&P500", [(p.day, Decimal(-i)) for i, p in enumerate(points)])]
    queued = []
    window.reader.run = lambda *a, **k: queued.append(a)  # 탭을 열 때의 실제 조회가 테스트 값을 덮지 않게
    window.show()  # 범례 표시 여부는 화면에 올라간 뒤에 정해진다
    window.tabs.setCurrentWidget(window.history_tab)
    queued.clear()
    window._show_asset_history(points, benchmarks)
    app.processEvents()
    assert [s.name() for s in window.history_chart.series()] == ["기초 자금", "총자산"]  # 기본은 금액
    compare = next(b for b in window.history_mode_group.buttons() if b.property("value") == "compare")
    compare.click()
    assert queued == []  # 보기만 바꾸고 다시 조회하지 않는다
    app.processEvents()
    assert [s.name() for s in window.history_chart.series()] == ["0%", "내 계좌", "코스피200", "S&P500"]
    visible = [m.label() for m in window.history_chart.legend().markers() if m.isVisible()]
    assert visible == ["내 계좌", "코스피200", "S&P500"]
    assert window.history_chart.axes(Qt.Vertical)[0].titleText() == "%"
    text = window.history_label.text()
    assert "내 계좌 +3.00%" in text and "코스피200 +3.00%" in text and "S&P500 -3.00%" in text


def test_history_compare_without_benchmarks_says_so(window, app):
    points = _history_points()
    window._show_asset_history(points, [])
    next(b for b in window.history_mode_group.buttons() if b.property("value") == "compare").click()
    assert [s.name() for s in window.history_chart.series()] == ["0%", "내 계좌"]
    assert "시장 지수를 받지 못했어요" in window.history_label.text()


def test_switching_history_view_replaces_the_chart(window, app):
    """금액 ↔ 수익률 전환 때 축을 떼고 붙이면 이전 눈금이 화면에 남아 겹쳐서, 차트를 새로 만들어 끼운다."""
    window.reader.run = lambda *a, **k: None
    window._show_asset_history(_history_points(), [])
    before = window.history_chart
    next(b for b in window.history_mode_group.buttons() if b.property("value") == "compare").click()
    assert window.history_chart is not before and window.history_view.chart() is window.history_chart
    assert len(window.history_chart.axes()) == 2


def test_replaced_history_charts_are_freed(window, app):
    """BM1: 바꿔 낀 이전 차트는 지워진다(지우지 않으면 갱신·전환마다 메모리가 쌓인다)."""
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    window.reader.run = lambda *a, **k: None
    old_charts = []
    for _ in range(5):
        old_charts.append(window.history_chart)
        window._show_asset_history(_history_points(), [])
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not any(shiboken6.isValid(chart) for chart in old_charts)
    assert shiboken6.isValid(window.history_chart)
def _stats_for(trades):
    from datetime import datetime
    from decimal import Decimal

    from mock_trading.domain import Currency
    from mock_trading.stats import trade_stats
    from mock_trading.storage import Repository

    repo = Repository()
    repo.reset_account(Decimal(10_000_000))
    for day, symbol, name, side, qty, price in trades:
        repo.add_trade(symbol=symbol, name=name, side=side, quantity=qty, price=Decimal(price), currency=Currency.KRW,
                       fx_rate=Decimal(1), amount_krw=Decimal(price * qty), fee_krw=Decimal(100),
                       executed_at=datetime.fromisoformat(day))
    return trade_stats(repo.trades())


def test_trade_stats_panel_shows_results(window, app):
    from mock_trading.domain import Side

    stats = _stats_for([
        ("2026-10-01", "A", "알파", Side.BUY, 10, 1000), ("2026-10-03", "A", "알파", Side.SELL, 10, 1200),
        ("2026-10-01", "B", "베타", Side.BUY, 10, 1000), ("2026-10-11", "B", "베타", Side.SELL, 10, 900),
    ])
    window._show_stats(stats)
    texts = {key: label.text() for key, label in window.stats_labels.items()}
    assert texts == {"profit": "+600원", "win_rate": "50.0%  (1/2)", "avg_win": "+1,800원",
                     "avg_loss": "-1,200원", "holding": "6.0일"}
    assert "최고 알파 +1,800원" in window.stats_note.text() and "최저 베타 -1,200원" in window.stats_note.text()
    assert window.stats_table.rowCount() == 2 and window.stats_table.item(0, 0).text() == "알파 (A)"


def test_trade_stats_panel_without_sells_and_single_sell(window, app):
    from mock_trading.domain import Side

    window._show_stats(_stats_for([("2026-10-01", "A", "알파", Side.BUY, 1, 1000)]))
    assert all(label.text() == "-" for label in window.stats_labels.values())
    assert window.stats_table.isHidden()
    window._show_stats(_stats_for([("2026-10-01", "A", "알파", Side.BUY, 1, 1000),
                                   ("2026-10-02", "A", "알파", Side.SELL, 1, 1100)]))
    note = window.stats_note.text()
    assert "최저" not in note and note.count("알파") == 1 and "알파 -100원" in note  # 한 건이면 최고·최저 없이 한 번


def test_trade_stats_with_long_names_keep_window_narrow(window, app):
    """긴 종목명·큰 금액에도 거래 내역 화면이 창 최소 폭(≤1000)을 늘리지 않는다."""
    from mock_trading.domain import Side
    from mock_trading.ui.theme import apply_theme

    font = app.font()
    apply_theme(app)
    try:
        long = "아주 긴 이름 Holdings Corporation International " * 2
        window._show_stats(_stats_for([
            ("2026-10-01", "A", long, Side.BUY, 1000, 999_000), ("2026-10-02", "A", long, Side.SELL, 1000, 999_900),
            ("2026-10-01", "B", long + "2", Side.BUY, 1000, 999_000), ("2026-10-02", "B", long + "2", Side.SELL, 1000, 1000),
        ]))
        window.tabs.setCurrentWidget(window.trades_page)
        app.processEvents()
        assert window.minimumSizeHint().width() <= 1000
    finally:
        app.setStyleSheet("")
        app.setFont(font)
