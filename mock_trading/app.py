"""앱 시작: 화면 띄우기, 자가 점검(--self-check). 실행은 `python -m mock_trading` 또는 실행 파일."""
import os
import sys
from decimal import Decimal
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .broker import Broker
from .market_data import YFinanceProvider
from .storage import Repository, default_db_path
from .ui.main_window import MainWindow
from .ui.theme import app_icon, apply_theme


def self_check(report: str) -> int:
    """실행 파일이 제대로 묶였는지 확인하고 결과를 파일에 쓴다(창 모드 실행 파일은 콘솔 출력이 없어서).

    단계: db(메모리 DB, 실제 계좌는 건드리지 않음) → gui(QApplication과 메인 창·차트를 실제로 만들어
    Qt 플랫폼·차트 플러그인이 묶였는지 확인, 창은 띄우지 않음) → quote(네트워크).
    종료 코드: 0 = db·gui 정상, 1 = db·gui 실패, 2 = 결과 파일을 쓰지 못함. 네트워크가 없어서 quote만 실패하면
    SKIPPED로 적고 0(묶음 자체는 정상이므로).
    """
    lines: list[str] = []
    code = 0
    try:
        repo = Repository()
        repo.reset_account(Decimal("1000000"))
        lines.append(f"db ok cash={repo.cash()}")

        from PySide6.QtCharts import QChart

        from . import market_data

        qt_app = QApplication.instance() or QApplication([])
        provider = market_data.YFinanceProvider()
        window = MainWindow(Broker(repo, provider))
        QChart()
        lines.append(f"gui ok platform={qt_app.platformName()}")
        window.deleteLater()
    except Exception as error:  # 묶음이 깨진 경우: 어느 단계인지 남긴다
        lines.append(f"FAILED {type(error).__name__}: {error}")
        code = 1
    else:
        try:
            quote = provider.get_quote("AAPL")
            lines.append(f"quote ok {quote.symbol} {quote.price} {quote.currency.value}")
        except Exception as error:
            lines.append(f"SKIPPED(network) {type(error).__name__}: {error}")
    try:
        Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return 2
    return code


def _use_own_taskbar_icon() -> None:
    """소스로 실행할 때 Windows 작업 표시줄에 python 아이콘 대신 앱 아이콘이 보이게 앱 ID를 따로 둔다."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Yongb15.MockTrading")
    except (AttributeError, OSError):
        pass  # 아이콘 표시용일 뿐이라 실패해도 앱 실행에는 영향 없음


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        return self_check(sys.argv[2])
    app = QApplication(sys.argv)
    app.setApplicationName("모의투자")
    apply_theme(app)
    _use_own_taskbar_icon()
    app.setWindowIcon(app_icon())
    # DB는 작업 스레드 하나에서만 쓰도록 Worker가 보장한다.
    repo = Repository(default_db_path(), check_same_thread=False)
    window = MainWindow(Broker(repo, YFinanceProvider()))
    window.show()
    if not window.start():  # 첫 실행에서 기초 자금 입력을 취소하면 바로 끝낸다
        return 0
    code = app.exec()
    if window.worker.wait(0) and window.reader.wait(0):
        repo.close()
        return code
    # 작업 스레드가 네트워크 응답을 기다리는 중이면 QThreadPool 소멸자가 끝까지 기다려 프로세스가 남는다.
    # DB 트랜잭션 도중이 아닐 때(잠금을 잡을 수 있을 때) 바로 끝낸다. 커밋된 거래는 이미 디스크에 있다.
    if repo.hold_for_exit(timeout=2.0):
        os._exit(code)
    return code


