from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import QDateTime, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..broker import AccountSummary, Broker
from ..domain import MAX_ORDER_QUANTITY, Position, Quote, Side, Trade
from ..market_data import PricePoint
from . import format as fmt
from .worker import Worker

AUTO_REFRESH_MS = 60_000
DEFAULT_INITIAL_CASH = 10_000_000


@dataclass
class _QuoteView:
    quote: Quote
    history: list[PricePoint]
    max_buy: int
    held: int


@dataclass
class _PortfolioView:
    summary: AccountSummary
    positions: list[Position]
    trades: list[Trade]


def _item(text: str, *, align_right: bool = False, color: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align_right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        item.setForeground(QColor(color))
    return item


class MainWindow(QMainWindow):
    def __init__(self, broker: Broker) -> None:
        super().__init__()
        self.broker = broker
        self.worker = Worker()
        self.current: _QuoteView | None = None

        self.setWindowTitle("모의투자")
        self.resize(1200, 780)
        self._build()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(AUTO_REFRESH_MS)

    # ------------------------------------------------------------------ 화면 구성
    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_summary())

        splitter = QSplitter(Qt.Vertical)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self._build_quote_panel())
        top.addWidget(self._build_chart())
        top.setSizes([380, 820])
        splitter.addWidget(top)
        splitter.addWidget(self._build_tables())
        splitter.setSizes([440, 300])
        layout.addWidget(splitter, 1)

        self.status = QLabel("종목 코드를 입력하세요. 예) 005930(삼성전자), AAPL")
        layout.addWidget(self.status)
        self.setCentralWidget(root)

    def _build_summary(self) -> QWidget:
        box = QGroupBox("내 계좌")
        grid = QGridLayout(box)
        self.summary_labels: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(
            [("total", "총자산"), ("cash", "현금"), ("stock", "주식 평가"), ("return", "총 수익률"), ("realized", "실현 손익")]
        ):
            caption = QLabel(title)
            caption.setStyleSheet("color: gray;")
            value = QLabel("-")
            value.setStyleSheet("font-size: 18px; font-weight: 600;")
            grid.addWidget(caption, 0, column)
            grid.addWidget(value, 1, column)
            self.summary_labels[key] = value

        buttons = QVBoxLayout()
        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.refresh_all)
        reset = QPushButton("계좌 초기화")
        reset.clicked.connect(self.reset_account)
        buttons.addWidget(refresh)
        buttons.addWidget(reset)
        holder = QWidget()
        holder.setLayout(buttons)
        grid.addWidget(holder, 0, 5, 2, 1)
        return box

    def _build_quote_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        search = QHBoxLayout()
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("종목 코드 (005930, AAPL ...)")
        self.symbol_input.setMaxLength(15)
        self.symbol_input.returnPressed.connect(self.load_quote)
        lookup = QPushButton("조회")
        lookup.clicked.connect(self.load_quote)
        search.addWidget(self.symbol_input, 1)
        search.addWidget(lookup)
        layout.addLayout(search)

        self.name_label = QLabel("-")
        self.name_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.price_label = QLabel("-")
        self.price_label.setStyleSheet("font-size: 26px; font-weight: 700;")
        self.change_label = QLabel("")
        layout.addWidget(self.name_label)
        layout.addWidget(self.price_label)
        layout.addWidget(self.change_label)

        order = QGroupBox("주문")
        form = QGridLayout(order)
        self.quantity = QSpinBox()
        self.quantity.setRange(1, MAX_ORDER_QUANTITY)
        self.quantity.valueChanged.connect(self._update_estimate)
        max_button = QPushButton("최대")
        max_button.clicked.connect(self._fill_max)
        form.addWidget(QLabel("수량"), 0, 0)
        form.addWidget(self.quantity, 0, 1)
        form.addWidget(max_button, 0, 2)

        self.estimate_label = QLabel("-")
        self.held_label = QLabel("-")
        form.addWidget(QLabel("예상 금액"), 1, 0)
        form.addWidget(self.estimate_label, 1, 1, 1, 2)
        form.addWidget(QLabel("보유 / 최대 매수"), 2, 0)
        form.addWidget(self.held_label, 2, 1, 1, 2)

        self.buy_button = QPushButton("매수")
        self.buy_button.setStyleSheet(f"background: {fmt.UP_COLOR}; color: white; padding: 8px; font-weight: 600;")
        self.buy_button.clicked.connect(lambda: self.place_order(Side.BUY))
        self.sell_button = QPushButton("매도")
        self.sell_button.setStyleSheet(f"background: {fmt.DOWN_COLOR}; color: white; padding: 8px; font-weight: 600;")
        self.sell_button.clicked.connect(lambda: self.place_order(Side.SELL))
        form.addWidget(self.buy_button, 3, 0, 1, 2)
        form.addWidget(self.sell_button, 3, 2)
        layout.addWidget(order)
        layout.addStretch(1)

        self._set_order_enabled(False)
        return panel

    def _build_chart(self) -> QWidget:
        self.chart = QChart()
        self.chart.legend().hide()
        self.chart.setTitle("최근 3개월 종가")
        view = QChartView(self.chart)
        view.setRenderHint(QPainter.Antialiasing)
        return view

    def _build_tables(self) -> QWidget:
        tabs = QTabWidget()
        self.holdings_table = self._table(["종목", "코드", "수량", "평균 매입가", "현재가", "평가 금액", "평가 손익", "수익률"])
        self.holdings_table.cellDoubleClicked.connect(self._open_holding)
        self.trades_table = self._table(["시간", "구분", "종목", "수량", "체결가", "환율", "체결 금액", "수수료"])
        tabs.addTab(self.holdings_table, "보유 종목")
        tabs.addTab(self.trades_table, "거래 내역")
        return tabs

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    # ------------------------------------------------------------------ 시작 / 계좌
    def start(self) -> None:
        if not self.broker.repo.has_account():
            self.show()
            if not self._ask_initial_cash(first_time=True):
                self.close()
                return
        self.refresh_all()

    def _ask_initial_cash(self, *, first_time: bool) -> bool:
        title = "기초 자금 받기" if first_time else "계좌 초기화"
        text = "연습에 쓸 가상 기초 자금(원)을 입력하세요." if first_time else "새 기초 자금(원). 기존 거래 내역은 모두 지워집니다."
        while True:
            amount, ok = QInputDialog.getInt(self, title, text, DEFAULT_INITIAL_CASH, 100_000, 2_000_000_000, 1_000_000)
            if not ok:
                return False
            try:
                self.broker.open_account(amount)
                return True
            except Exception as error:
                QMessageBox.warning(self, title, str(error))

    def reset_account(self) -> None:
        answer = QMessageBox.question(self, "계좌 초기화", "보유 종목과 거래 내역이 모두 삭제됩니다. 계속할까요?")
        if answer != QMessageBox.Yes:
            return
        # 초기화는 화면 스레드에서 DB를 직접 쓰므로, 자동 갱신을 멈추고 진행 중인 작업이 끝난 뒤 실행한다.
        self.timer.stop()
        self.worker.wait()
        try:
            if self._ask_initial_cash(first_time=False):
                self.current = None
                self._set_order_enabled(False)
                self.refresh_all()
        finally:
            self.timer.start(AUTO_REFRESH_MS)

    # ------------------------------------------------------------------ 데이터 불러오기
    def refresh_all(self) -> None:
        self.load_portfolio()
        if self.current:
            self.load_quote(self.current.quote.symbol)

    def load_portfolio(self) -> None:
        def job() -> _PortfolioView:
            positions = self.broker.positions()
            return _PortfolioView(self.broker.summary(positions), positions, self.broker.repo.trades())

        self.worker.run(job, self._show_portfolio, self._show_error)

    def load_quote(self, symbol: str | None = None) -> None:
        symbol = symbol or self.symbol_input.text()
        if not symbol.strip():
            return
        self.status.setText(f"{symbol} 시세를 불러오는 중...")

        def job() -> _QuoteView:
            quote = self.broker.provider.get_quote(symbol)
            history = self.broker.provider.get_history(quote.symbol)
            held = self.broker.repo.holdings().get(quote.symbol)
            return _QuoteView(quote, history, self.broker.max_buy_quantity(quote), held.quantity if held else 0)

        self.worker.run(job, self._show_quote, self._show_error)

    # ------------------------------------------------------------------ 주문
    def place_order(self, side: Side) -> None:
        if not self.current:
            return
        quote = self.current.quote
        quantity = self.quantity.value()
        verb = "매수" if side is Side.BUY else "매도"
        answer = QMessageBox.question(
            self,
            f"{verb} 확인",
            f"{quote.name} ({quote.symbol})\n{quantity:,}주를 현재가에 {verb}할까요?\n예상 금액: {self.estimate_label.text()}",
        )
        if answer != QMessageBox.Yes:
            return
        self._set_order_enabled(False)
        action = self.broker.buy if side is Side.BUY else self.broker.sell

        def done(trade: Trade) -> None:
            self.status.setText(
                f"{verb} 체결: {trade.name} {trade.quantity:,}주 @ {fmt.price(trade.price, trade.currency)}"
                f" (수수료 {fmt.won(trade.fee_krw)})"
            )
            self.refresh_all()

        def failed(message: str) -> None:
            self._set_order_enabled(True)
            QMessageBox.warning(self, f"{verb} 실패", message)

        self.worker.run(lambda: action(quote.symbol, quantity), done, failed)

    def _fill_max(self) -> None:
        if self.current:
            self.quantity.setValue(max(1, self.current.max_buy))

    def _update_estimate(self) -> None:
        if not self.current:
            self.estimate_label.setText("-")
            return
        quote = self.current.quote
        amount = quote.price * self.quantity.value()
        self.estimate_label.setText(fmt.price(amount, quote.currency))

    def _set_order_enabled(self, enabled: bool) -> None:
        self.buy_button.setEnabled(enabled)
        self.sell_button.setEnabled(enabled)

    def _open_holding(self, row: int, _column: int) -> None:
        symbol = self.holdings_table.item(row, 1).text()
        self.symbol_input.setText(symbol)
        self.load_quote(symbol)

    # ------------------------------------------------------------------ 결과 표시
    def _show_error(self, message: str) -> None:
        self.status.setText(message)
        self._set_order_enabled(self.current is not None)

    def _show_quote(self, view: _QuoteView) -> None:
        self.current = view
        quote = view.quote
        self.name_label.setText(f"{quote.name}  ({quote.symbol})")
        self.price_label.setText(fmt.price(quote.price, quote.currency))
        change = quote.change_rate
        self.change_label.setText(f"전일 대비 {fmt.percent(change)}")
        self.change_label.setStyleSheet(f"color: {fmt.color_for(change)};")
        self.held_label.setText(f"{view.held:,}주 / {view.max_buy:,}주")
        self._update_estimate()
        self._set_order_enabled(True)
        self._draw_chart(quote, view.history)
        self.status.setText(f"{quote.symbol} 시세 갱신 완료 (무료 시세는 최대 약 15분 지연될 수 있습니다)")

    def _draw_chart(self, quote: Quote, history: list[PricePoint]) -> None:
        self.chart.removeAllSeries()
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)
        if not history:
            return
        series = QLineSeries()
        first, last = history[0].close, history[-1].close
        series.setColor(QColor(fmt.UP_COLOR if last >= first else fmt.DOWN_COLOR))
        for point in history:
            series.append(QDateTime(point.at).toMSecsSinceEpoch(), float(point.close))
        self.chart.addSeries(series)

        x_axis = QDateTimeAxis()
        x_axis.setFormat("MM/dd")
        y_axis = QValueAxis()
        y_axis.setLabelFormat("%.0f" if quote.currency.value == "KRW" else "%.2f")
        closes = [float(p.close) for p in history]
        padding = (max(closes) - min(closes)) * 0.05 or 1
        y_axis.setRange(min(closes) - padding, max(closes) + padding)
        self.chart.addAxis(x_axis, Qt.AlignBottom)
        self.chart.addAxis(y_axis, Qt.AlignLeft)
        series.attachAxis(x_axis)
        series.attachAxis(y_axis)
        self.chart.setTitle(f"{quote.name} 최근 3개월 종가")

    def _show_portfolio(self, view: _PortfolioView) -> None:
        summary = view.summary
        self.summary_labels["total"].setText(fmt.won(summary.total))
        self.summary_labels["cash"].setText(fmt.won(summary.cash))
        self.summary_labels["stock"].setText(fmt.won(summary.stock_value))
        rate = summary.total_return_rate
        self.summary_labels["return"].setText(fmt.percent(rate))
        self.summary_labels["return"].setStyleSheet(
            f"font-size: 18px; font-weight: 600; color: {fmt.color_for(rate)};"
        )
        self.summary_labels["realized"].setText(fmt.signed_won(summary.realized_profit))

        self.holdings_table.setRowCount(len(view.positions))
        for row, position in enumerate(sorted(view.positions, key=lambda p: p.value_krw, reverse=True)):
            h, q = position.holding, position.quote
            color = fmt.color_for(position.profit_krw)
            values = [
                _item(h.name),
                _item(h.symbol),
                _item(f"{h.quantity:,}", align_right=True),
                _item(fmt.won(h.avg_cost_krw), align_right=True),
                _item(fmt.price(q.price, q.currency), align_right=True),
                _item(fmt.won(position.value_krw), align_right=True),
                _item(fmt.signed_won(position.profit_krw), align_right=True, color=color),
                _item(fmt.percent(position.profit_rate), align_right=True, color=color),
            ]
            for column, item in enumerate(values):
                self.holdings_table.setItem(row, column, item)

        trades = list(reversed(view.trades))
        self.trades_table.setRowCount(len(trades))
        for row, trade in enumerate(trades):
            buy = trade.side is Side.BUY
            values = [
                _item(trade.executed_at.strftime("%Y-%m-%d %H:%M:%S")),
                _item("매수" if buy else "매도", color=fmt.UP_COLOR if buy else fmt.DOWN_COLOR),
                _item(f"{trade.name} ({trade.symbol})"),
                _item(f"{trade.quantity:,}", align_right=True),
                _item(fmt.price(trade.price, trade.currency), align_right=True),
                _item("-" if trade.fx_rate == Decimal(1) else f"{trade.fx_rate:,.2f}", align_right=True),
                _item(fmt.won(trade.amount_krw), align_right=True),
                _item(fmt.won(trade.fee_krw), align_right=True),
            ]
            for column, item in enumerate(values):
                self.trades_table.setItem(row, column, item)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 이름 규칙)
        self.timer.stop()
        self.worker.wait()
        super().closeEvent(event)
