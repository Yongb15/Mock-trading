"""실행: python -m mock_trading"""
import sys

from PySide6.QtWidgets import QApplication

from .broker import Broker
from .market_data import YFinanceProvider
from .storage import Repository, default_db_path
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("모의투자")
    # DB는 작업 스레드 하나에서만 쓰도록 Worker가 보장한다.
    repo = Repository(default_db_path(), check_same_thread=False)
    window = MainWindow(Broker(repo, YFinanceProvider()))
    window.show()
    if not window.start():  # 첫 실행에서 기초 자금 입력을 취소하면 바로 끝낸다
        return 0
    code = app.exec()
    # 작업이 아직 끝나지 않았으면(네트워크 대기) 연결을 닫지 않고 프로세스 종료에 맡긴다.
    if window.worker.wait(0):
        repo.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
