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
    window._show_asset_history = lambda points: shown.append(window._view_generation)
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
