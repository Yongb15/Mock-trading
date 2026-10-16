"""앱 시작: 화면 띄우기, 자가 점검(--self-check). 실행은 `python -m mock_trading` 또는 실행 파일."""
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .broker import Broker
from .market_data import YFinanceProvider
from .storage import Repository, default_db_path
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def self_check(report: str) -> int:
    """실행 파일이 제대로 묶였는지 확인: 화면을 띄우지 않고 DB·시세·화면 모듈을 한 번씩 써 보고 결과를 파일에 쓴다.

    창 모드 실행 파일은 콘솔 출력이 없어서 결과를 파일로 남긴다. 실제 계좌 DB는 건드리지 않는다(메모리 DB).
    """
    lines = []
    try:
        from .market_data import YFinanceProvider as Provider
        from .ui.main_window import MainWindow as _Window  # noqa: F401  화면 모듈이 묶였는지

        repo = Repository()  # 메모리 DB
        repo.reset_account(__import__("decimal").Decimal(1_000_000))
        lines.append(f"db ok cash={repo.cash()}")
        quote = Provider().get_quote("AAPL")
        lines.append(f"quote ok {quote.symbol} {quote.price} {quote.currency.value}")
        code = 0
    except Exception as error:  # 어떤 단계에서 실패했는지 남긴다
        lines.append(f"FAILED {type(error).__name__}: {error}")
        code = 1
    Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        return self_check(sys.argv[2])
    app = QApplication(sys.argv)
    app.setApplicationName("모의투자")
    apply_theme(app)
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


