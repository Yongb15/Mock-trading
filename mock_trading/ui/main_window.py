from __future__ import annotations

import html
from pathlib import Path
from datetime import date
from dataclasses import dataclass
from decimal import Decimal

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import QDateTime, Qt, QTime, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
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
    QComboBox,
    QFileDialog,
    QDoubleSpinBox,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..broker import PRICE_SLIPPAGE_LIMIT, AccountSummary, Broker, OrderEvent
from ..domain import (
    MAX_ORDER_QUANTITY,
    Currency,
    LimitOrder,
    OrderStatus,
    Position,
    Quote,
    Side,
    Trade,
    TradingError,
    krw,
)
from ..history import AssetPoint, DailyCloseCache, load_asset_history
from ..market_data import PricePoint
from ..export import write_trades_csv
from ..market_rank import Market, MarketRanking, RankBy, Ranking
from ..watchlist import Watchlist, WatchRow
from . import format as fmt
from .theme import style_axes, style_chart
from .worker import Worker

AUTO_REFRESH_MS = 60_000
CLOSE_WAIT_MS = 3_000
DEFAULT_INITIAL_CASH = 10_000_000


@dataclass
class _QuoteView:
    quote: Quote
    history: list[PricePoint]
    max_buy: int
    held: int
    fx_rate: Decimal
    watched: bool = False
    pending_sell: int = 0


@dataclass
class _PortfolioView:
    summary: AccountSummary
    positions: list[Position]
    trades: list[Trade]


def _plain_label(text: str) -> QLabel:
    """종목명처럼 외부에서 온 문자열은 <a>, <img> 같은 태그가 해석되지 않게 일반 텍스트로만 표시한다."""
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setOpenExternalLinks(False)
    return label


def _order_button_style(color: str) -> str:
    # 배경색을 직접 지정하면 비활성 상태도 같은 색으로 보이므로 :disabled 모양을 따로 둔다.
    return (
        f"QPushButton {{ background: {color}; color: white; border-radius: 14px; padding: 12px;"
        " font-size: 16px; font-weight: 700; border: 2px solid transparent; }"
        # 빨강·파랑 위에서도 보이도록 포커스 테두리는 어두운 색
        "QPushButton:focus { border: 2px solid #191F28; }"
        "QPushButton:disabled { background: #E5E8EB; color: #8B95A1; }"
    )


def _confirm(parent: QWidget, title: str, text: str) -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.No, parent)
    box.setTextFormat(Qt.PlainText)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


def _warn(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Ok, parent)
    box.setTextFormat(Qt.PlainText)
    box.exec()


def _item(text: str, *, align_right: bool = False, color: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if align_right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        item.setForeground(QColor(color))
    return item


class MainWindow(QMainWindow):
    def __init__(self, broker: Broker, watchlist: Watchlist | None = None) -> None:
        super().__init__()
        self.broker = broker
        self.watchlist = watchlist or Watchlist(broker.repo, broker.provider)
        self.market_ranking = MarketRanking(broker.provider)
        # 주문·초기화·계좌·시세처럼 순서가 중요한 일은 한 줄로, 관심 종목·자산 추이처럼 읽기만 하는 느린 조회는
        # 따로 돌려 주문이 그 뒤에서 기다리지 않게 한다(DB는 Repository 잠금으로 직렬화된다).
        self.worker = Worker()
        self.reader = Worker(threads=2)
        self._view_generation = 0  # 계좌를 초기화할 때마다 올린다 → 초기화 전에 시작한 조회 결과는 버린다
        self.current: _QuoteView | None = None
        self._order_pending = False
        self._reset_pending = False
        self._watch_pending = False
        self._watch_loading = False
        self._portfolio_loading = False
        self._history_loading = False
        self._history_reload = False
        self._watch_reload = False
        self._rank_loading = False
        self._rank_reload = False
        self._rank_market = Market.KR
        self._rank_by = RankBy.GAINERS
        self._orders_checking = False
        self._unseen_order_events = 0
        self._order_notice: QMessageBox | None = None
        self._history_cache = DailyCloseCache()

        self.setWindowTitle("모의투자")
        self.resize(1200, 780)
        self._build()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)  # start()에서 계좌가 생긴 뒤 켠다

    # ------------------------------------------------------------------ 화면 구성
    def _build(self) -> None:
        """왼쪽 메뉴 + 오른쪽 페이지. 페이지는 탭 묶음(self.tabs)에 들어 있고 탭 줄은 숨긴 채 메뉴로 바꾼다."""
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(12, 12, 16, 12)
        outer.setSpacing(16)

        content = QVBoxLayout()
        content.setSpacing(8)
        content.addWidget(self._build_pages(), 1)
        self.status = _plain_label("종목 코드를 입력하세요. 예) 005930(삼성전자), AAPL")
        self.status.setObjectName("status")
        content.addWidget(self.status)

        outer.addWidget(self._build_nav())
        outer.addLayout(content, 1)
        self.setCentralWidget(root)
        self._sync_nav()

    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nav.setObjectName("nav")
        nav.setFixedWidth(168)
        layout = QVBoxLayout(nav)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
        brand = QLabel("모의투자")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(12)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index in range(self.tabs.count()):
            button = QPushButton(self.tabs.tabText(index))
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, i=index: self.tabs.setCurrentIndex(i))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        return nav

    def _sync_nav(self) -> None:
        """메뉴 버튼의 선택 표시와 글자(지정가 주문 건수 배지 포함)를 페이지 상태에 맞춘다."""
        if not hasattr(self, "nav_buttons"):
            return
        for index, button in enumerate(self.nav_buttons):
            button.setText(self.tabs.tabText(index))
            button.setChecked(index == self.tabs.currentIndex())
        # 포커스가 메뉴에 있는데 다른 페이지로 바뀌었으면(표에서 종목 열기 등) 포커스도 선택된 메뉴로 옮긴다.
        # 그대로 두면 이전 메뉴에 포커스 테두리가 남아 선택된 메뉴가 둘처럼 보인다.
        current = self.nav_buttons[self.tabs.currentIndex()]
        if any(b.hasFocus() for b in self.nav_buttons) and not current.hasFocus():
            current.setFocus(Qt.OtherFocusReason)

    def _build_summary(self) -> QWidget:
        box = QGroupBox("내 계좌")
        grid = QGridLayout(box)
        self.summary_labels: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(
            [("total", "총자산"), ("cash", "현금"), ("stock", "주식 평가"), ("return", "총 수익률"), ("realized", "실현 손익")]
        ):
            caption = QLabel(title)
            caption.setObjectName("caption")
            value = QLabel("-")
            value.setObjectName("heroNumber" if key == "total" else "bigNumber")
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
        panel.setObjectName("card")  # 흰 카드(theme.py)
        panel.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)

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

        self.name_label = _plain_label("-")
        self.name_label.setStyleSheet("font-size: 16px; font-weight: 600; color: #4E5968;")
        self.name_label.setWordWrap(True)
        self.price_label = QLabel("-")
        self.price_label.setStyleSheet("font-size: 32px; font-weight: 800;")
        self.change_label = QLabel("")
        name_row = QHBoxLayout()
        name_row.addWidget(self.name_label, 1)
        self.watch_button = QPushButton("☆ 관심")
        self.watch_button.setCheckable(True)
        self.watch_button.setEnabled(False)
        self.watch_button.clicked.connect(self.toggle_watch)
        name_row.addWidget(self.watch_button)
        layout.addLayout(name_row)
        layout.addWidget(self.price_label)
        layout.addWidget(self.change_label)

        order = QGroupBox("주문")
        order.setObjectName("flat")  # 카드 안의 묶음: 테두리·배경 없이
        form = QGridLayout(order)
        self.quantity = QSpinBox()
        self.quantity.setRange(1, MAX_ORDER_QUANTITY)
        self.quantity.valueChanged.connect(self._update_estimate)
        max_button = QPushButton("최대")
        max_button.clicked.connect(self._fill_max)
        self.order_type = QComboBox()
        self.order_type.addItems(["시장가", "지정가"])
        self.order_type.currentIndexChanged.connect(self._on_order_type_changed)
        self.limit_price = QDoubleSpinBox()
        self.limit_price.setRange(0, 1_000_000_000)
        self.limit_price.setGroupSeparatorShown(True)
        self.limit_price.setMinimumWidth(90)  # 최대값(10억) 자릿수 때문에 칸이 넓어져 창 최소 폭을 키우지 않게
        self.limit_price.setEnabled(False)
        self.limit_price.valueChanged.connect(self._update_estimate)
        self._limit_symbol: str | None = None
        form.addWidget(QLabel("주문 방식"), 0, 0)
        form.addWidget(self.order_type, 0, 1)
        form.addWidget(self.limit_price, 0, 2)
        form.addWidget(QLabel("수량"), 1, 0)
        form.addWidget(self.quantity, 1, 1)
        form.addWidget(max_button, 1, 2)

        self.estimate_label = QLabel("-")
        self.estimate_label.setWordWrap(True)
        self.held_label = QLabel("-")
        form.addWidget(QLabel("예상 금액"), 2, 0)
        form.addWidget(self.estimate_label, 2, 1, 1, 2)
        form.addWidget(QLabel("보유 / 최대 매수"), 3, 0)
        form.addWidget(self.held_label, 3, 1, 1, 2)

        self.buy_button = QPushButton("매수")
        self.buy_button.setStyleSheet(_order_button_style(fmt.UP_COLOR))
        self.buy_button.clicked.connect(lambda: self.place_order(Side.BUY))
        self.sell_button = QPushButton("매도")
        self.sell_button.setStyleSheet(_order_button_style(fmt.DOWN_COLOR))
        self.sell_button.clicked.connect(lambda: self.place_order(Side.SELL))
        form.addWidget(self.buy_button, 4, 0, 1, 2)
        form.addWidget(self.sell_button, 4, 2)
        layout.addWidget(order)
        layout.addStretch(1)

        self._set_order_enabled(False)
        return panel

    def _build_chart(self) -> QWidget:
        self.chart = QChart()
        style_chart(self.chart)
        self.chart.legend().hide()
        self.chart.setTitle("최근 3개월 종가")
        view = QChartView(self.chart)
        view.setFocusPolicy(Qt.NoFocus)  # 보기만 하는 차트는 Tab 순서에서 뺀다
        view.setRenderHint(QPainter.Antialiasing)
        return view

    def _build_rank_page(self) -> QWidget:
        page = self.rank_page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        controls = QHBoxLayout()
        self.rank_market_group = QButtonGroup(self)
        self.rank_by_group = QButtonGroup(self)
        for group, options, current in (
            (self.rank_market_group, [(Market.KR, "한국"), (Market.US, "미국")], self._rank_market),
            (self.rank_by_group, [(RankBy.GAINERS, "급상승"), (RankBy.LOSERS, "급하락"), (RankBy.VOLUME, "거래량")],
             self._rank_by),
        ):
            group.setExclusive(True)
            for value, label in options:
                button = QPushButton(label)
                button.setObjectName("chip")
                button.setCheckable(True)
                button.setChecked(value is current)
                button.setProperty("value", value.value)
                group.addButton(button)
                controls.addWidget(button)
            controls.addSpacing(16)
        self.rank_market_group.buttonClicked.connect(self._on_rank_option)
        self.rank_by_group.buttonClicked.connect(self._on_rank_option)
        controls.addStretch(1)
        refresh = QPushButton("새로 받기")
        refresh.clicked.connect(lambda: self.load_ranking(force=True))
        controls.addWidget(refresh)
        layout.addLayout(controls)

        self.rank_info = _plain_label("-")
        self.rank_info.setObjectName("caption")
        self.rank_info.setWordWrap(True)
        layout.addWidget(self.rank_info)

        self.rank_table = self._table(["순위", "종목", "코드", "현재가", "전일 대비", "거래량"])
        self.rank_table.itemActivated.connect(lambda item: self._open_ranked(item.row()))
        layout.addWidget(self.rank_table, 1)
        return page

    def _build_pages(self) -> QWidget:
        tabs = QTabWidget()
        tabs.tabBar().hide()  # 페이지 전환은 왼쪽 메뉴로
        # 숨긴 탭 줄이 포커스를 받으면 Tab이 메뉴에서 빠져나가지 못한다(키보드 트랩) → 둘 다 포커스를 받지 않게
        tabs.setFocusPolicy(Qt.NoFocus)
        tabs.tabBar().setFocusPolicy(Qt.NoFocus)
        self.tabs = tabs

        # 내 계좌: 요약 카드 + 보유 종목
        account_page = self.account_page = QWidget()
        account_layout = QVBoxLayout(account_page)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.setSpacing(12)
        account_layout.addWidget(self._build_summary())
        holdings_box = QGroupBox("보유 종목")
        holdings_layout = QVBoxLayout(holdings_box)
        holdings_layout.setContentsMargins(8, 8, 8, 8)

        # 주식 주문: 시세·주문 + 차트
        order_page = self.order_page = QSplitter(Qt.Horizontal)
        order_page.addWidget(self._build_quote_panel())
        order_page.addWidget(self._build_chart())
        order_page.setSizes([400, 800])

        self.holdings_table = self._table(["종목", "코드", "수량", "평균 매입가", "현재가", "평가 금액", "평가 손익", "수익률"])
        # 더블클릭과 Enter 모두로 연다
        self.holdings_table.itemActivated.connect(lambda item: self._open_holding(item.row(), item.column()))
        self.trades_table = self._table(["시간", "구분", "종목", "수량", "체결가", "환율", "체결 금액", "수수료"])
        self.watch_table = self._table(["종목", "코드", "현재가", "전일 대비"])
        self.watch_table.itemActivated.connect(lambda item: self._open_watch(item.row(), item.column()))
        QShortcut(QKeySequence.Delete, self.watch_table, self._remove_selected_watch, context=Qt.WidgetShortcut)
        watch_tab = self.watch_tab = QWidget()
        watch_layout = QVBoxLayout(watch_tab)
        watch_layout.setContentsMargins(0, 0, 0, 0)
        watch_layout.addWidget(self.watch_table, 1)
        remove = QPushButton("선택한 종목 빼기 (Delete)")
        remove.clicked.connect(self._remove_selected_watch)
        watch_layout.addWidget(remove, 0, Qt.AlignRight)
        holdings_layout.addWidget(self.holdings_table, 1)
        account_layout.addWidget(holdings_box, 1)
        tabs.addTab(account_page, "내 계좌")
        tabs.addTab(order_page, "주식 주문")
        tabs.addTab(self._build_rank_page(), "시장 순위")
        trades_page = self.trades_page = QWidget()
        trades_layout = QVBoxLayout(trades_page)
        trades_layout.setContentsMargins(0, 0, 0, 0)
        trades_layout.addWidget(self.trades_table, 1)
        export = QPushButton("CSV로 내보내기")
        export.clicked.connect(self.export_trades)
        trades_layout.addWidget(export, 0, Qt.AlignRight)
        tabs.addTab(trades_page, "거래 내역")
        tabs.addTab(watch_tab, "관심 종목")

        history_tab = self.history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_label = _plain_label("-")
        history_layout.addWidget(self.history_label)
        self.history_chart = QChart()
        style_chart(self.history_chart)
        self.history_chart.setTitle("날짜별 총자산")
        history_view = QChartView(self.history_chart)
        history_view.setFocusPolicy(Qt.NoFocus)
        history_view.setRenderHint(QPainter.Antialiasing)
        history_layout.addWidget(history_view, 1)
        tabs.addTab(history_tab, "자산 추이")

        orders_tab = self.orders_tab = QWidget()
        orders_layout = QVBoxLayout(orders_tab)
        orders_layout.setContentsMargins(0, 0, 0, 0)
        self.orders_table = self._table(["번호", "주문 시각", "구분", "종목", "수량", "지정가", "상태", "비고"])
        QShortcut(QKeySequence.Delete, self.orders_table, self._cancel_selected_orders, context=Qt.WidgetShortcut)
        orders_layout.addWidget(self.orders_table, 1)
        cancel = QPushButton("선택한 대기 주문 취소 (Delete)")
        cancel.clicked.connect(self._cancel_selected_orders)
        orders_layout.addWidget(cancel, 0, Qt.AlignRight)
        tabs.addTab(orders_tab, "지정가 주문")

        tabs.currentChanged.connect(lambda _: self._load_visible_tab())
        tabs.currentChanged.connect(lambda _: self.tabs.currentWidget() is self.orders_tab and self._clear_order_badge())
        tabs.currentChanged.connect(lambda _: self._sync_nav())
        return tabs

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(44)
        table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    # ------------------------------------------------------------------ 시작 / 계좌
    def start(self) -> bool:
        """계좌가 없으면 기초 자금을 받는다. 취소하면 False → 호출한 쪽이 앱을 끝낸다.

        자동 갱신 타이머는 계좌가 생긴 뒤에만 켠다(입력창이 떠 있는 동안 갱신 작업이 돌지 않게).
        """
        if not self.broker.repo.has_account():
            self.show()
            if not self._ask_initial_cash():
                return False
        self.refresh_all()
        self.timer.start(AUTO_REFRESH_MS)
        return True

    def _ask_initial_cash(self) -> bool:
        """첫 실행 전용. 아직 작업 스레드에 아무 작업도 없으므로 화면 스레드에서 바로 계좌를 만든다."""
        title = "기초 자금 받기"
        text = "연습에 쓸 가상 기초 자금(원)을 입력하세요."
        while True:
            # QInputDialog.getInt는 32비트 정수라 20억까지만 받는다. 연습용으로는 충분하다.
            amount, ok = QInputDialog.getInt(self, title, text, DEFAULT_INITIAL_CASH, 100_000, 2_000_000_000, 1_000_000)
            if not ok:
                return False
            try:
                self.broker.open_account(amount)
                return True
            except Exception as error:
                _warn(self, title, str(error))

    def reset_account(self) -> None:
        if self._order_pending or self._reset_pending:
            self.status.setText("주문이나 초기화를 처리하는 중에는 계좌를 초기화할 수 없습니다.")
            return
        if not _confirm(self, "계좌 초기화", "보유 종목과 거래 내역이 모두 삭제됩니다. 계속할까요?"):
            return
        amount, ok = QInputDialog.getInt(
            self, "계좌 초기화", "새 기초 자금(원). 기존 거래 내역은 모두 지워집니다.",
            DEFAULT_INITIAL_CASH, 100_000, 2_000_000_000, 1_000_000,
        )
        if not ok:
            return

        # 초기화도 작업 스레드 대기열에 넣는다. 앞서 들어간 작업(시세 조회 등)이 모두 끝난 뒤 실행되고,
        # 그 결과가 초기화 뒤에 화면을 이전 계좌 상태로 되돌리지 않도록 current를 초기화 완료 시점에 비운다.
        symbol = self.current.quote.symbol if self.current else None

        def done(_: object) -> None:
            self._reset_pending = False
            self._view_generation += 1
            self.current = None
            self._set_order_enabled(False)
            self.status.setText(f"계좌를 {fmt.won(Decimal(amount))}으로 초기화했습니다.")
            self.refresh_all()
            if symbol:
                # 시세 패널의 "보유 N주 / 최대 M주"가 이전 계좌 값으로 남지 않게 새 계좌 기준으로 다시 불러온다.
                self.load_quote(symbol)

        def failed(message: str) -> None:
            self._reset_pending = False
            self._set_order_enabled(True)
            _warn(self, "계좌 초기화", message)

        # 초기화가 끝날 때까지 주문 버튼을 끈다. 그 사이 도착하는 시세 결과도 버튼을 다시 켜지 못한다.
        self._reset_pending = True
        self._set_order_enabled(False)
        self.status.setText("계좌를 초기화하는 중...")
        self.worker.run(lambda: self.broker.open_account(amount), done, failed)

    # ------------------------------------------------------------------ 데이터 불러오기
    def refresh_all(self) -> None:
        self.check_limit_orders()  # 체결이 계좌 갱신에 바로 반영되도록 맨 앞에
        self.load_portfolio()
        if self.current:
            self.load_quote(self.current.quote.symbol)
        self.load_orders()
        # 관심 종목·자산 추이는 가장 덜 급하므로 맨 뒤에, 탭이 보일 때만.
        self._load_visible_tab()

    def _load_visible_tab(self) -> None:
        current = self.tabs.currentWidget()
        if current is self.watch_tab:
            self.load_watchlist()
        elif current is self.history_tab:
            self.load_asset_history()
        elif current is self.rank_page:
            self.load_ranking()

    def load_portfolio(self) -> None:
        if self._portfolio_loading:  # 직전 갱신이 아직 대기열에 있으면 또 넣지 않는다(느린 네트워크에서 쌓임 방지)
            return
        self._portfolio_loading = True

        def job() -> _PortfolioView:
            positions = self.broker.positions()
            return _PortfolioView(self.broker.summary(positions), positions, self.broker.repo.trades())

        def done(view: _PortfolioView) -> None:
            self._portfolio_loading = False
            self._show_portfolio(view)

        def failed(message: str) -> None:
            self._portfolio_loading = False
            self._show_error(message)

        self.worker.run(job, done, failed)

    def load_quote(self, symbol: str | None = None) -> None:
        symbol = symbol or self.symbol_input.text()
        if not symbol.strip():
            return
        self.status.setText(f"{symbol} 시세를 불러오는 중...")

        def job() -> _QuoteView:
            quote = self.broker.provider.get_quote(symbol)
            history = self.broker.provider.get_history(quote.symbol)
            fx_rate = self.broker.fx_rate_for(quote.currency)
            held = self.broker.repo.holdings().get(quote.symbol)
            return _QuoteView(
                quote,
                history,
                self.broker.max_buy_quantity(quote),
                held.quantity if held else 0,
                fx_rate,
                self.watchlist.contains(quote.symbol),
                self.broker.pending_sell_quantity(quote.symbol),
            )

        self.worker.run(job, self._show_quote, self._show_error)

    # ------------------------------------------------------------------ 내보내기
    def export_trades(self) -> None:
        default = Path.home() / "Documents" / f"모의투자_거래내역_{date.today():%Y%m%d}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "거래 내역 내보내기", str(default), "CSV 파일 (*.csv)")
        if not path:
            return

        def job() -> int:
            try:
                return write_trades_csv(self.broker.repo.trades(), path)
            except OSError:
                raise TradingError("파일을 저장할 수 없습니다. 같은 파일이 엑셀 등에서 열려 있지 않은지, 저장 위치를 확인하세요.") from None

        def done(count: int) -> None:
            self.status.setText(f"거래 {count}건을 내보냈습니다: {path}")

        def failed(message: str) -> None:
            _warn(self, "내보내기 실패", message)

        self.reader.run(job, done, failed)

    # ------------------------------------------------------------------ 시장 순위
    def _on_rank_option(self, _button=None) -> None:
        self._rank_market = Market(self.rank_market_group.checkedButton().property("value"))
        self._rank_by = RankBy(self.rank_by_group.checkedButton().property("value"))
        self.load_ranking()  # 기억해 둔 시세로 정렬만 다시(5분이 지났으면 새로 받음)

    def load_ranking(self, *, force: bool = False) -> None:
        if self._rank_loading:
            self._rank_reload = True
            return
        self._rank_loading = True
        market, by = self._rank_market, self._rank_by
        self.rank_info.setText("시세를 받는 중...")

        def done(ranking: Ranking) -> None:
            self._rank_loading = False
            if (ranking.market, ranking.by) == (self._rank_market, self._rank_by):
                self._show_ranking(ranking)
            if self._rank_reload or (ranking.market, ranking.by) != (self._rank_market, self._rank_by):
                self._rank_reload = False
                self.load_ranking()  # 그 사이 시장·기준을 바꿨으면 바뀐 것으로 한 번 더

        def failed(message: str) -> None:
            self._rank_loading = False
            self._rank_reload = False
            self.rank_info.setText(message)

        # 느린 일괄 조회라 주문 대기열이 아니라 읽기 전용 작업 풀에서
        self.reader.run(lambda: self.market_ranking.ranking(market, by, force=force), done, failed)

    def _show_ranking(self, ranking: Ranking) -> None:
        market = "한국" if ranking.market is Market.KR else "미국"
        if ranking.priced == 0:
            # "조건에 맞는 종목 없음"과 구분: 아예 시세를 못 받은 상태
            self.rank_info.setText("시세를 받지 못했어요. 잠시 후 자동으로 다시 시도해요.")
        else:
            basis = f"{ranking.as_of:%m/%d} 종가 기준" if ranking.as_of else "최근 종가 기준"
            stale = " · 방금 새로 받지 못해 이전 값을 보여 주는 중" if ranking.last_failed else ""
            self.rank_info.setText(
                f"{market} 주요 종목 {ranking.universe_size}개 중 순위 (시장 전체 순위가 아님) · "
                f"시세 {ranking.priced}개 · {basis} ({ranking.fetched_at:%H:%M}에 받음){stale} · "
                "무료 시세는 약 15분 지연될 수 있어요"
            )
        self.rank_table.setRowCount(len(ranking.rows))
        for row, entry in enumerate(ranking.rows):
            color = fmt.color_for(entry.change_rate)
            values = [
                _item(str(entry.rank), align_right=True),
                _item(entry.name),
                _item(entry.symbol),
                _item(fmt.price(entry.price, entry.currency), align_right=True),
                _item(fmt.percent(entry.change_rate), align_right=True, color=color),
                _item(f"{entry.volume:,}" if entry.volume is not None else "-", align_right=True),
            ]
            for column, cell in enumerate(values):
                self.rank_table.setItem(row, column, cell)
        if not ranking.rows and ranking.priced:
            self.rank_info.setText(self.rank_info.text() + " · 조건에 맞는 종목이 없어요")

    def _open_ranked(self, row: int) -> None:
        symbol = self.rank_table.item(row, 2).text()
        self.tabs.setCurrentWidget(self.order_page)
        self.symbol_input.setText(symbol)
        self.load_quote(symbol)

    # ------------------------------------------------------------------ 자산 추이
    def load_asset_history(self) -> None:
        if self._history_loading:
            self._history_reload = True  # 진행 중인 조회는 이 요청 전 상태일 수 있으니 끝나면 한 번 더
            return
        self._history_loading = True

        generation = self._view_generation

        def job() -> list[AssetPoint]:
            return load_asset_history(self.broker.repo, self.broker.provider, date.today(), self._history_cache)

        def done(points: list[AssetPoint]) -> None:
            self._history_loading = False
            if generation != self._view_generation:
                self._load_visible_tab()  # 초기화 전 계좌의 결과 → 버리고 새 계좌로 다시
                return
            self._show_asset_history(points)  # 받은 결과는 먼저 보여 주고
            if self._history_reload:          # 그 사이 들어온 요청이 있었으면 한 번 더
                self._history_reload = False
                self.load_asset_history()

        def failed(message: str) -> None:
            self._history_loading = False
            self._history_reload = False
            self._show_error(message)

        self.reader.run(job, done, failed)

    def _show_asset_history(self, points: list[AssetPoint]) -> None:
        chart = self.history_chart
        chart.removeAllSeries()
        for axis in chart.axes():
            chart.removeAxis(axis)
        if not points:
            self.history_label.setText("-")
            return

        total = QLineSeries()
        total.setName("총자산")
        last = points[-1]
        total.setColor(QColor(fmt.color_for(last.return_rate) or "#495057"))
        principal = QLineSeries()
        principal.setName("기초 자금")
        pen = QPen(QColor("#adb5bd"))
        pen.setStyle(Qt.DashLine)
        principal.setPen(pen)
        if len(points) < 2:
            total.setPointsVisible(True)  # 오늘 연 계좌는 점이 하나라 선이 그려지지 않는다
        values = []
        for point in points:
            x = QDateTime(point.day, QTime(0, 0)).toMSecsSinceEpoch()
            # 원 단위는 축 숫자가 너무 길어서 만원 단위로 그린다
            total.append(x, float(point.total) / 10_000)
            principal.append(x, float(point.initial_cash) / 10_000)
            values += [float(point.total) / 10_000, float(point.initial_cash) / 10_000]
        chart.addSeries(principal)
        chart.addSeries(total)

        x_axis = QDateTimeAxis()
        span = (points[-1].day - points[0].day).days
        x_axis.setFormat("yy/MM/dd" if span > 365 else "MM/dd")  # 1년이 넘으면 연도까지
        first = QDateTime(points[0].day, QTime(0, 0))
        x_axis.setRange(first.addDays(-1), QDateTime(points[-1].day, QTime(0, 0)).addDays(1))
        y_axis = QValueAxis()
        # 변동이 1만원 남짓인 평평한 구간에서는 눈금이 같은 숫자로 찍히지 않게 소수 한 자리까지
        # (라벨 형식 문자열에는 한글을 쓸 수 없어 단위는 축 제목으로)
        y_axis.setLabelFormat("%.1f" if max(values) - min(values) < 10 else "%.0f")
        y_axis.setTitleText("만원")
        padding = (max(values) - min(values)) * 0.1 or max(values) * 0.01 or 1
        y_axis.setRange(min(values) - padding, max(values) + padding)
        chart.addAxis(x_axis, Qt.AlignBottom)
        chart.addAxis(y_axis, Qt.AlignLeft)
        for series in (principal, total):
            series.attachAxis(x_axis)
            series.attachAxis(y_axis)
        style_axes(chart)

        # 최고·최저는 그날 종가로 평가한 날만으로 계산한다(대신 쓴 체결가가 튀는 값을 만들지 않게).
        priced = [p for p in points if not p.missing_price] or points
        best = max(priced, key=lambda p: p.total)
        worst = min(priced, key=lambda p: p.total)
        note = "  · 종가가 없는 날은 마지막 체결가로 계산" if any(p.missing_price for p in points) else ""
        self.history_label.setText(
            f"{points[0].day:%Y-%m-%d} ~ {last.day:%Y-%m-%d}  |  현재 {fmt.won(last.total)} ({fmt.percent(last.return_rate)})"
            f"  |  최고 {fmt.won(best.total)} ({best.day:%m/%d})  ·  최저 {fmt.won(worst.total)} ({worst.day:%m/%d}){note}"
        )
        self.history_label.setStyleSheet(f"color: {fmt.color_for(last.return_rate)};")

    # ------------------------------------------------------------------ 관심 종목
    def load_watchlist(self) -> None:
        if self._watch_loading:  # 직전 갱신이 아직 진행 중이면 또 넣지 않고, 끝난 뒤 한 번 더
            self._watch_reload = True
            return
        self._watch_loading = True

        generation = self._view_generation

        def done(rows: list[WatchRow]) -> None:
            self._watch_loading = False
            if generation != self._view_generation:
                self._load_visible_tab()
                return
            self._show_watchlist(rows)
            if self._watch_reload:
                self._watch_reload = False
                self.load_watchlist()

        def failed(message: str) -> None:
            self._watch_loading = False
            self._watch_reload = False
            self._show_error(message)

        self.reader.run(self.watchlist.rows, done, failed)

    def toggle_watch(self) -> None:
        if not self.current:
            return
        if self._watch_pending:
            return
        symbol = self.current.quote.symbol
        adding = not self.current.watched
        self._watch_pending = True
        self.watch_button.setEnabled(False)

        def job() -> bool:
            if adding:
                self.watchlist.add(symbol)
            else:
                self.watchlist.remove(symbol)
            return adding

        def done(added: bool) -> None:
            self._watch_pending = False
            if self.current and self.current.quote.symbol == symbol:
                self.current.watched = added
                self._show_watch_state()
            self.status.setText(f"{symbol}을(를) 관심 종목에 {'담았습니다' if added else '뺐습니다'}.")
            self.load_watchlist()

        def failed(message: str) -> None:
            self._watch_pending = False
            self._show_watch_state()
            _warn(self, "관심 종목", message)

        self.worker.run(job, done, failed)

    def _remove_selected_watch(self) -> None:
        rows = sorted({index.row() for index in self.watch_table.selectedIndexes()})
        symbols = [self.watch_table.item(row, 1).text() for row in rows]
        if not symbols:
            self.status.setText("뺄 종목을 표에서 선택하세요.")
            return

        def job() -> list[str]:
            for symbol in symbols:
                self.watchlist.remove(symbol)
            return symbols

        def done(removed: list[str]) -> None:
            if self.current and self.current.quote.symbol in removed:
                self.current.watched = False
                self._show_watch_state()
            self.status.setText(f"관심 종목에서 {len(removed)}개를 뺐습니다.")
            self.load_watchlist()

        self.worker.run(job, done, self._show_error)

    def _open_watch(self, row: int, _column: int) -> None:
        self.tabs.setCurrentWidget(self.order_page)
        symbol = self.watch_table.item(row, 1).text()
        self.symbol_input.setText(symbol)
        self.load_quote(symbol)

    def _show_watch_state(self) -> None:
        watched = bool(self.current and self.current.watched)
        self.watch_button.setEnabled(self.current is not None and not self._watch_pending)
        self.watch_button.setChecked(watched)
        self.watch_button.setText("★ 관심" if watched else "☆ 관심")

    def _show_watchlist(self, rows: list[WatchRow]) -> None:
        self.watch_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            item, quote = entry.item, entry.quote
            change = quote.change_rate if quote else None
            color = fmt.color_for(change)
            values = [
                _item(item.name),
                _item(item.symbol),
                _item(fmt.price(quote.price, quote.currency) if quote else "시세 없음", align_right=True),
                _item(fmt.percent(change), align_right=True, color=color),
            ]
            for column, cell in enumerate(values):
                self.watch_table.setItem(row, column, cell)

    # ------------------------------------------------------------------ 지정가 주문
    def _is_limit(self) -> bool:
        return self.order_type.currentIndex() == 1

    def _on_order_type_changed(self) -> None:
        self.limit_price.setEnabled(self._is_limit())
        self._update_estimate()
        self._set_order_enabled(True)

    def check_limit_orders(self) -> None:
        if self._orders_checking:
            return
        self._orders_checking = True

        def done(events: list[OrderEvent]) -> None:
            self._orders_checking = False
            self.load_orders()  # "확인 1회" 표시도 바로 보이게
            if events:
                self._notify_order_events(events)
                self.load_portfolio()
                self._load_visible_tab()  # 자산 추이를 보고 있었다면 방금 체결도 그래프에

        def failed(message: str) -> None:
            self._orders_checking = False
            self._show_error(message)

        self.worker.run(self.broker.check_limit_orders, done, failed)

    def _notify_order_events(self, events: list[OrderEvent]) -> None:
        """백그라운드에서 일어난 체결·거부를 놓치지 않게: 막지 않는 알림 창과 탭 제목의 건수."""
        lines = [self._describe_event(e) for e in events]
        if self._order_notice is None:
            self._order_notice = QMessageBox(QMessageBox.Information, "지정가 주문", "", QMessageBox.Ok, self)
            self._order_notice.setTextFormat(Qt.PlainText)
            self._order_notice.setModal(False)
            self._order_notice.finished.connect(lambda _: self._order_notice.setText(""))  # 닫으면 비운다
        previous = self._order_notice.text() if self._order_notice.isVisible() else ""
        # 아직 안 본 앞선 알림은 지우지 않고 이어 붙인다
        self._order_notice.setText("\n".join([previous, *lines]) if previous else "\n".join(lines))
        self._order_notice.show()
        if self.tabs.currentWidget() is not self.orders_tab:
            self._unseen_order_events += len(events)
            self.tabs.setTabText(self.tabs.indexOf(self.orders_tab), f"지정가 주문 ({self._unseen_order_events})")
            self._sync_nav()

    def _clear_order_badge(self) -> None:
        self._unseen_order_events = 0
        self.tabs.setTabText(self.tabs.indexOf(self.orders_tab), "지정가 주문")
        self._sync_nav()

    @staticmethod
    def _describe_event(event: OrderEvent) -> str:
        order, verb = event.order, "매수" if event.order.side is Side.BUY else "매도"
        if event.trade:
            return f"지정가 {verb} 체결: {order.name} {order.quantity:,}주 @ {fmt.price(event.trade.price, order.currency)}"
        return f"지정가 {verb} 거부: {order.name} — {event.note}"

    def load_orders(self) -> None:
        self.worker.run(lambda: self.broker.repo.orders(), self._show_orders, self._show_error)

    def _show_orders(self, orders: list[LimitOrder]) -> None:
        labels = {
            OrderStatus.OPEN: ("대기", ""),
            OrderStatus.FILLED: ("체결", fmt.UP_COLOR),
            OrderStatus.CANCELLED: ("취소", "#868e96"),
            OrderStatus.REJECTED: ("거부", fmt.DOWN_COLOR),
        }
        self.orders_table.setRowCount(len(orders))
        for row, order in enumerate(orders):
            status, color = labels[order.status]
            if order.status is OrderStatus.OPEN and order.armed:
                status = "대기 (확인 1회)"  # 다음 확인에서도 조건이 맞으면 체결
            buy = order.side is Side.BUY
            values = [
                _item(str(order.id), align_right=True),
                _item(order.created_at.strftime("%Y-%m-%d %H:%M")),
                _item("매수" if buy else "매도", color=fmt.UP_COLOR if buy else fmt.DOWN_COLOR),
                _item(f"{order.name} ({order.symbol})"),
                _item(f"{order.quantity:,}", align_right=True),
                _item(fmt.price(order.limit_price, order.currency), align_right=True),
                self._status_item(status, color, order.status),
                _item(order.note),
            ]
            for column, cell in enumerate(values):
                self.orders_table.setItem(row, column, cell)

    @staticmethod
    def _status_item(text: str, color: str, status: OrderStatus) -> QTableWidgetItem:
        """보이는 문구와 별개로 실제 상태 값을 담아 둔다(문구를 바꿔도 취소 판단이 흔들리지 않게)."""
        item = _item(text, color=color)
        item.setData(Qt.UserRole, status.value)
        return item

    def _cancel_selected_orders(self) -> None:
        rows = sorted({index.row() for index in self.orders_table.selectedIndexes()})
        if not rows:
            self.status.setText("취소할 주문을 표에서 선택하세요.")
            return
        # 이미 체결·취소·거부된 행은 빼고 대기 중인 주문만 센다(보이는 문구가 아니라 상태 칸에 담아 둔 값으로).
        ids = [int(self.orders_table.item(row, 0).text()) for row in rows
               if self.orders_table.item(row, 6).data(Qt.UserRole) == OrderStatus.OPEN.value]
        if not ids:
            self.status.setText("선택한 주문 중 대기 중인 주문이 없습니다.")
            return
        # Delete 키 하나로 되돌릴 수 없는 취소가 일어나지 않게 확인한다(관심 종목 빼기는 다시 담으면 되니 확인 없음).
        if not _confirm(self, "대기 주문 취소", f"선택한 대기 주문 {len(ids)}건을 취소할까요?"):
            return

        def job() -> int:
            return sum(1 for order_id in ids if self.broker.cancel_order(order_id))

        def done(count: int) -> None:
            self.status.setText(f"대기 주문 {count}건을 취소했습니다." if count else "취소할 수 있는 대기 주문이 없습니다.")
            self.load_orders()

        self.worker.run(job, done, self._show_error)

    def _place_limit_order(self, side: Side) -> None:
        quote = self.current.quote
        quantity = self.quantity.value()
        limit = Decimal(str(round(self.limit_price.value(), self.limit_price.decimals())))
        verb = "매수" if side is Side.BUY else "매도"
        condition = "이하로 내려가면" if side is Side.BUY else "이상으로 오르면"
        amount, fee = self._estimate(side)
        message = (
            f"{quote.name} ({quote.symbol})\n"
            f"현재가 {fmt.price(quote.price, quote.currency)}\n\n"
            f"가격이 {fmt.price(limit, quote.currency)} {condition} {quantity:,}주를 지정가로 {verb}합니다.\n"
            f"예상 금액: {fmt.won(amount)} (수수료·세금 약 {fmt.won(fee)})\n"
            + (f"적용 환율: {self.current.fx_rate:,}원/달러 (주문 기준값으로 저장)\n" if quote.currency is Currency.USD else "")
            + "\n앱이 켜져 있는 동안 1분마다 확인하고, 약 1분 간격으로 두 번 연속 조건이 맞을 때 체결합니다.\n"
            "취소할 때까지 유지되며, 체결할 때 현금이나 보유 수량이 부족하면 거부됩니다."
            + ("\n미국 주식은 체결 때 환율이 주문 때보다 10% 넘게 다르면 체결을 미룹니다." if quote.currency is Currency.USD else "")
        )
        worse = (side is Side.BUY and limit > quote.price) or (side is Side.SELL and limit < quote.price)
        if worse:
            gap = abs(limit - quote.price) / quote.price * 100
            direction = "높은" if side is Side.BUY else "낮은"
            message = (
                f"주의: 지정가가 현재가보다 {gap:.1f}% {direction} 가격이라, 곧 지정가로 {verb}하게 됩니다"
                f"(현재가보다 불리).\n\n" + message
            )
        if not _confirm(self, f"지정가 {verb} 확인", message):
            return
        self._order_pending = True
        self._set_order_enabled(False)

        def done(order: LimitOrder) -> None:
            self._order_pending = False
            self._set_order_enabled(True)
            self.status.setText(f"지정가 {verb} 주문 #{order.id}을(를) 걸어 두었습니다.")
            self.refresh_all()  # 이미 조건이 맞으면 바로 체결된다

        def failed(message: str) -> None:
            self._order_pending = False
            self._set_order_enabled(True)
            _warn(self, f"지정가 {verb} 실패", message)

        expected_price, expected_fx = quote.price, self.current.fx_rate
        self.worker.run(
            lambda: self.broker.place_limit_order(
                side, quote.symbol, quantity, limit, expected_price=expected_price, expected_fx=expected_fx
            ),
            done,
            failed,
        )

    # ------------------------------------------------------------------ 주문
    def place_order(self, side: Side) -> None:
        if not self.current or self._order_pending or self._reset_pending:
            return
        if self._is_limit():
            self._place_limit_order(side)
            return
        quote = self.current.quote
        quantity = self.quantity.value()
        verb = "매수" if side is Side.BUY else "매도"
        amount, fee = self._estimate(side)
        message = (
            f"{quote.name} ({quote.symbol})\n"
            f"{quantity:,}주를 {fmt.price(quote.price, quote.currency)}에 {verb}할까요?\n\n"
            f"예상 금액: {fmt.won(amount)}\n"
            f"예상 수수료·세금: {fmt.won(fee)}\n"
            + (f"적용 환율: {self.current.fx_rate:,}원/달러\n" if quote.currency.value == "USD" else "")
            + f"가격이나 환율이 {PRICE_SLIPPAGE_LIMIT}% 넘게 바뀌면 체결하지 않습니다."
            + (f"\n\n대기 중인 지정가 판매 {self.current.pending_sell:,}주가 있습니다. 지금 팔면 그 주문은 체결 때 거부될 수 있습니다."
               if side is Side.SELL and self.current.pending_sell else "")
        )
        if not _confirm(self, f"{verb} 확인", message):
            return
        self._order_pending = True
        self._set_order_enabled(False)
        action = self.broker.buy if side is Side.BUY else self.broker.sell
        expected_price, expected_fx = quote.price, self.current.fx_rate

        def done(trade: Trade) -> None:
            self._order_pending = False
            self.status.setText(
                f"{verb} 체결: {trade.name} {trade.quantity:,}주 @ {fmt.price(trade.price, trade.currency)}"
                f" (수수료 {fmt.won(trade.fee_krw)})"
            )
            self.refresh_all()

        def failed(message: str) -> None:
            self._order_pending = False
            self._set_order_enabled(True)
            _warn(self, f"{verb} 실패", message)
            self.refresh_all()

        self.worker.run(
            lambda: action(quote.symbol, quantity, expected_price=expected_price, expected_fx=expected_fx), done, failed
        )

    def _estimate(self, side: Side) -> tuple[Decimal, Decimal]:
        """화면과 확인창에 보여줄 원화 금액과 수수료. 실제 체결과 같은 규칙(broker.fees)으로 계산한다."""
        view = self.current
        price = Decimal(str(self.limit_price.value())) if self._is_limit() else view.quote.price
        amount = krw(price * view.fx_rate * self.quantity.value())
        return amount, self.broker.fees.fee(side, view.quote.currency, amount)

    def _fill_max(self) -> None:
        if not self.current:
            return
        if self.current.max_buy == 0:
            self.status.setText("현금이 부족해 1주도 살 수 없습니다.")
            return
        self.quantity.setValue(self.current.max_buy)

    def _update_estimate(self) -> None:
        if not self.current:
            self.estimate_label.setText("-")
            return
        amount, fee = self._estimate(Side.BUY)
        basis = "지정가 기준, " if self._is_limit() else ""
        self.estimate_label.setText(f"{fmt.won(amount + fee)} ({basis}매수 수수료 포함)")

    def _set_order_enabled(self, enabled: bool) -> None:
        """주문이 진행 중이면 다른 작업 결과가 먼저 도착해도 버튼을 켜지 않는다(중복 주문 방지)."""
        view = self.current
        enabled = enabled and view is not None and not self._order_pending and not self._reset_pending
        # 지정가 매수는 지금 현금으로 살 수 있는지 주문할 때 따로 확인하므로 최대 수량과 무관하게 켠다.
        self.buy_button.setEnabled(enabled and (view.max_buy > 0 or self._is_limit()))
        self.sell_button.setEnabled(enabled and view.held > 0)

    def _open_holding(self, row: int, _column: int) -> None:
        self.tabs.setCurrentWidget(self.order_page)
        symbol = self.holdings_table.item(row, 1).text()
        self.symbol_input.setText(symbol)
        self.load_quote(symbol)

    # ------------------------------------------------------------------ 결과 표시
    def _show_error(self, message: str) -> None:
        self.status.setText(message)
        self._set_order_enabled(True)

    def _show_quote(self, view: _QuoteView) -> None:
        self.current = view
        quote = view.quote
        self.name_label.setText(f"{quote.name}  ({quote.symbol})")
        self.price_label.setText(fmt.price(quote.price, quote.currency))
        change = quote.change_rate
        self.change_label.setText(f"전일 대비 {fmt.percent(change)}")
        self.change_label.setStyleSheet(f"color: {fmt.color_for(change)};")
        self.held_label.setText(f"{view.held:,}주 / {view.max_buy:,}주")
        if self._limit_symbol != quote.symbol:  # 같은 종목을 갱신할 때는 사용자가 입력한 지정가를 유지
            self._limit_symbol = quote.symbol
            if quote.currency is Currency.KRW:
                self.limit_price.setDecimals(0)
            else:
                self.limit_price.setDecimals(4 if quote.price < 1 else 2)  # 1달러 미만은 0.00으로 반올림되지 않게
            self.limit_price.setValue(float(quote.price))
        self._update_estimate()
        self._set_order_enabled(True)
        self._show_watch_state()
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
        style_axes(self.chart)
        self.chart.setTitle(html.escape(f"{quote.name} 최근 3개월 종가"))  # QChart 제목은 HTML을 해석한다

    def _show_portfolio(self, view: _PortfolioView) -> None:
        summary = view.summary
        self.summary_labels["total"].setText(fmt.won(summary.total))
        self.summary_labels["cash"].setText(fmt.won(summary.cash))
        self.summary_labels["stock"].setText(fmt.won(summary.stock_value))
        rate = summary.total_return_rate
        self.summary_labels["return"].setText(fmt.percent(rate))
        self.summary_labels["return"].setStyleSheet(f"color: {fmt.color_for(rate)};")
        self.summary_labels["realized"].setText(fmt.signed_won(summary.realized_profit))

        self.holdings_table.setRowCount(len(view.positions))
        for row, position in enumerate(sorted(view.positions, key=lambda p: p.value_krw, reverse=True)):
            h, q = position.holding, position.quote
            priced = position.has_price
            color = fmt.color_for(position.profit_krw)
            values = [
                _item(h.name),
                _item(h.symbol),
                _item(f"{h.quantity:,}", align_right=True),
                _item(fmt.won(h.avg_cost_krw), align_right=True),
                _item(fmt.price(q.price, q.currency) if priced else "시세 없음", align_right=True),
                _item(fmt.won(position.value_krw), align_right=True),
                _item(fmt.signed_won(position.profit_krw) if priced else "-", align_right=True, color=color),
                _item(fmt.percent(position.profit_rate) if priced else "-", align_right=True, color=color),
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
        self.worker.wait(CLOSE_WAIT_MS)  # 네트워크가 끊겨도 닫기가 멈추지 않게
        self.reader.wait(CLOSE_WAIT_MS)
        super().closeEvent(event)
