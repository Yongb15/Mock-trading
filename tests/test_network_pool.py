"""느린 읽기 전용 조회(관심 종목·자산 추이)를 주문 대기열과 분리한 것의 테스트."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading  # noqa: E402
import time  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mock_trading.broker import Broker  # noqa: E402
from mock_trading.storage import Repository  # noqa: E402
from mock_trading.ui.main_window import MainWindow  # noqa: E402

from .test_broker import FakeProvider  # noqa: E402


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    broker = Broker(Repository(check_same_thread=False), FakeProvider())
    broker.open_account(10_000_000)
    w = MainWindow(broker)
    yield w
    w.worker.wait(5000)
    w.reader.wait(5000)
    w.close()


def pump(app, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def test_slow_watchlist_does_not_delay_orders(window, app):
    """관심 종목 조회가 3초 걸려도 그 사이 주문은 바로 처리된다(예전엔 그 뒤에서 기다렸다)."""
    release = threading.Event()

    def slow_rows():
        release.wait(3)
        return []

    window.watchlist.rows = slow_rows
    window.tabs.setCurrentWidget(window.watch_tab)  # 느린 조회 시작
    started = time.time()
    done = []
    window.worker.run(
        lambda: window.broker.buy("005930", 1, expected_price=Decimal("70000")),
        lambda trade: done.append(time.time() - started),
    )
    while not done and time.time() - started < 5:
        pump(app, 0.05)
    release.set()
    assert done and done[0] < 1.0
    assert len(window.broker.repo.trades()) == 1


def test_result_started_before_reset_is_discarded(window, app):
    """초기화 전에 시작한 자산 추이 결과가 초기화 뒤에 도착하면 그리지 않고 새 계좌로 다시 불러온다."""
    shown = []
    window._show_asset_history = lambda points, benchmarks=None: shown.append(window._view_generation)
    release = threading.Event()
    calls = []

    import mock_trading.ui.main_window as mw

    original = mw.load_asset_history

    def slow_then_fast(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            release.wait(3)
        return original(*args, **kwargs)

    mw.load_asset_history = slow_then_fast
    try:
        window.tabs.setCurrentWidget(window.history_tab)  # 1번째(느림) 시작
        pump(app, 0.1)
        window._view_generation += 1                       # 그 사이 초기화가 끝난 상황
        release.set()
        deadline = time.time() + 5
        while len(calls) < 2 and time.time() < deadline:
            pump(app, 0.05)
        window.reader.wait(3000)
        pump(app, 0.2)
    finally:
        mw.load_asset_history = original
    assert len(calls) == 2              # 버리고 다시 불러옴
    assert shown == [1]                 # 새 세대의 결과만 그림


def test_batch_downloads_never_overlap():
    """yf.download는 전역 상태를 써서 동시에 부르면 섞인다 → 잠금으로 하나씩."""
    from mock_trading.domain import Currency
    from mock_trading.market_data import YFinanceProvider

    active, peak = [0], [0]
    lock = threading.Lock()

    class FakeYF:
        @staticmethod
        def download(symbols, **kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            import pandas as pd
            return {"Close": pd.DataFrame({s: [1.0, 1.0] for s in symbols},
                                          index=pd.to_datetime(["2026-10-14", "2026-10-15"]))}

    provider = YFinanceProvider.__new__(YFinanceProvider)
    provider._yf = FakeYF()
    provider._names = {}
    threads = [threading.Thread(target=provider.get_quotes, args=([("AAPL", "Apple", Currency.USD)],))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


def test_limit_check_does_not_wait_long_for_another_download():
    """Mock_Sequrity NP1: 관심 종목·자산 추이 다운로드가 잠금을 잡고 있어도 지정가 확인은 1.5초 안에 돌아오고,
    실패로 기록하지 않아 다음 주기에 바로 다시 확인한다."""
    from datetime import datetime

    from mock_trading.broker import Broker
    from mock_trading.domain import Currency, Quote, Side
    from mock_trading.market_data import _DOWNLOAD_LOCK, YFinanceProvider

    class Provider(YFinanceProvider):
        def __init__(self):
            self._names = {}
            self.fake = FakeProvider()

        def get_quote(self, symbol):
            return self.fake.get_quote(symbol)

        def get_usd_krw(self):
            return self.fake.get_usd_krw()

    broker = Broker(Repository(), Provider())
    broker.open_account(10_000_000)
    broker.place_limit_order(Side.BUY, "005930", 1, Decimal("70000"), expected_price=Decimal("70000"))
    holder_ready, release = threading.Event(), threading.Event()

    def hold_lock():
        with _DOWNLOAD_LOCK:
            holder_ready.set()
            release.wait(10)

    t = threading.Thread(target=hold_lock)
    t.start()
    holder_ready.wait(5)
    started = time.time()
    try:
        assert broker.check_limit_orders(datetime(2026, 10, 15, 12)) == []
        assert time.time() - started < 2.5
        assert broker._quote_failed_at == {}  # "이번엔 못 봄"이지 실패가 아니다
    finally:
        release.set()
        t.join()
    _ = (Currency, Quote)


def test_batch_logs_hide_only_this_batchs_symbols(caplog):
    """Mock_Sequrity NP2 + Mock_QA Z1: 일괄 조회 중에 숨기는 건 (1) 조회한 스레드의 요약과 (2) 이번 조회 종목이 들어간
    메시지뿐이다. yfinance 내부 스레드가 찍는 종목별 오류도 숨기고, 다른 종목·다른 스레드 로그는 그대로 보인다."""
    import logging

    from mock_trading.market_data import _quiet_batch_failures

    logger = logging.getLogger("yfinance")
    inside, release = threading.Event(), threading.Event()
    errors = []

    def batch():
        try:
            with _quiet_batch_failures(["ZZZZ9"]):
                logger.error("1 Failed download")                          # 호출 스레드의 요약 → 숨김
                worker = threading.Thread(target=lambda: logger.error("$ZZZZ9: No data found"))
                worker.start()                                              # yfinance 내부 스레드 흉내 → 숨김
                worker.join()
                inside.set()
                release.wait(5)
        except BaseException as error:  # 스레드 예외가 조용히 묻히지 않게
            errors.append(error)
            inside.set()

    with caplog.at_level(logging.WARNING, logger="yfinance"):
        t = threading.Thread(target=batch)
        t.start()
        inside.wait(5)
        logger.error("AAPL: order-path error")                              # 다른 종목·다른 스레드 → 보임
        release.set()
        t.join()
    assert errors == []
    messages = [r.getMessage() for r in caplog.records]
    assert "AAPL: order-path error" in messages
    assert "1 Failed download" not in messages
    assert "$ZZZZ9: No data found" not in messages


def test_result_is_shown_before_reloading(window, app):
    """Mock_QA Z2: 진행 중에 요청이 들어와도 받은 결과는 먼저 보여 주고, 그다음 1회 다시 불러온다."""
    shown, calls = [], []
    window._show_watchlist = lambda rows: shown.append(len(calls))
    window.watchlist.rows = lambda: calls.append(1) or []
    window.tabs.setCurrentWidget(window.watch_tab)   # 1번째 조회 시작
    window.load_watchlist()                           # 진행 중 요청 → 끝난 뒤 1회
    for _ in range(5):
        window.reader.wait(3000)
        pump(app, 0.05)
    assert len(calls) == 2
    assert shown == [1, 2]                            # 두 결과 모두 표시(첫 결과를 버리지 않음)


def test_one_letter_symbol_does_not_hide_unrelated_logs(caplog):
    """Mock_QA: "T" 같은 한 글자 종목이 있어도 다른 종목 메시지는 숨기지 않는다(코드 경계 비교)."""
    import logging

    from mock_trading.market_data import _quiet_batch_failures

    logger = logging.getLogger("yfinance")
    done = threading.Event()

    def other_thread():
        logger.error("AAPL: Too Many Requests")   # 'T'가 들어 있지만 종목 T가 아님 → 보여야 함
        logger.error("$TSLA: possibly delisted")   # Mock_Sequrity RN1 재현 → 보여야 함
        logger.error("Failed to get ticker 'NVDA' 429")
        logger.error("$T: No data found")         # 종목 T → 숨김
        logger.error("Failed to get ticker 'T'")  # 종목 T → 숨김
        done.set()

    with caplog.at_level(logging.WARNING, logger="yfinance"):
        with _quiet_batch_failures(["T"]):
            t = threading.Thread(target=other_thread)
            t.start()
            t.join()
    messages = [r.getMessage() for r in caplog.records]
    for kept in ["AAPL: Too Many Requests", "$TSLA: possibly delisted", "Failed to get ticker 'NVDA' 429"]:
        assert kept in messages
    assert "$T: No data found" not in messages
    assert "Failed to get ticker 'T'" not in messages
