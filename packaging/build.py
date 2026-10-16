"""Windows용 실행 폴더 만들기: python packaging/build.py → dist/MockTrading/MockTrading.exe

한 파일(--onefile)로 묶으면 실행할 때마다 Qt를 임시 폴더에 풀어서 느리므로 폴더로 만든다.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    for leftover in (ROOT / "build", ROOT / "dist" / "MockTrading"):
        shutil.rmtree(leftover, ignore_errors=True)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",                 # 콘솔 창 없이
        "--name", "MockTrading",
        "--specpath", str(ROOT / "build"),  # 생성되는 .spec은 build/에(저장소에 남기지 않음)
        "--paths", str(ROOT),
        # yfinance가 쓰는 HTTP 라이브러리의 인증서·바이너리
        "--collect-all", "curl_cffi",
        "--collect-data", "certifi",
        # 쓰지 않는 큰 모듈은 빼서 용량을 줄인다
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        str(ROOT / "packaging" / "run_app.py"),
    ]
    print(" ".join(command))
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
