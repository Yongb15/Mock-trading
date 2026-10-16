"""실행 파일(PyInstaller)의 시작점. 패키지 안의 상대 import가 동작하도록 패키지 밖에서 부른다."""
import sys

from mock_trading.app import main

if __name__ == "__main__":
    sys.exit(main())
