"""실행: python -m mock_trading"""
import os
import sys

from PySide6.QtWidgets import QApplication

from .broker import Broker
from .market_data import YFinanceProvider
from .storage import Repository, default_db_path
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main() -> int:
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
    if window.worker.wait(0):
        repo.close()
        return code
    # 작업 스레드가 네트워크 응답을 기다리는 중이면 QThreadPool 소멸자가 끝까지 기다려 프로세스가 남는다.
    # DB 트랜잭션 도중이 아닐 때(잠금을 잡을 수 있을 때) 바로 끝낸다. 커밋된 거래는 이미 디스크에 있다.
    if repo.hold_for_exit(timeout=2.0):
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
