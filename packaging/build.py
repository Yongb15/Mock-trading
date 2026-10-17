"""Windows용 실행 폴더 만들기: python packaging/build.py → dist/MockTrading/MockTrading.exe

한 파일(--onefile)로 묶으면 실행할 때마다 Qt를 임시 폴더에 풀어서 느리므로 폴더로 만든다.
"""
import hashlib
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-build.lock"
_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\\#]+)")  # tests/test_locks.py와 같은 규칙


def lock_mismatches(lock: Path = LOCK) -> list[str]:
    """지금 환경에 설치된 버전이 빌드 lock과 다른 패키지(없거나 다르면). 리뷰한 lock = 배포물을 빌드 단계에서 강제한다."""
    problems = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line.strip())
        if not match:
            continue
        name, wanted = match.groups()
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != wanted:
            problems.append(f"{name}: lock {wanted}, 설치됨 {installed or '없음'}")
    return problems


def make_icon(path: Path) -> None:
    """앱 창과 같은 그림(theme.draw_icon)으로 exe 아이콘(.ico, 여러 크기)을 만든다. pillow가 필요하다."""
    from io import BytesIO

    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    sys.path.insert(0, str(ROOT))
    from mock_trading.ui.theme import ICON_SIZES, draw_icon

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (QPixmap에 필요)
    images = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.ReadWrite)
        draw_icon(size).save(buffer, "PNG")
        images.append(Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA"))
    path.parent.mkdir(parents=True, exist_ok=True)
    largest = images[-1]
    largest.save(path, format="ICO", sizes=[i.size for i in images], append_images=images[:-1])


def main() -> int:
    problems = lock_mismatches()
    if problems:
        print("빌드 환경이 requirements-build.lock과 다릅니다(새 가상환경에서 아래로 설치한 뒤 빌드하세요):")
        print("  pip install --require-hashes --only-binary :all: -r requirements-build.lock")
        for problem in problems:
            print("  - " + problem)
        return 3
    for leftover in (ROOT / "build", ROOT / "dist" / "MockTrading"):
        shutil.rmtree(leftover, ignore_errors=True)
    icon = ROOT / "build" / "MockTrading.ico"
    make_icon(icon)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",                 # 콘솔 창 없이
        "--name", "MockTrading",
        "--icon", str(icon),
        "--specpath", str(ROOT / "build"),  # 생성되는 .spec은 build/에(저장소에 남기지 않음)
        "--paths", str(ROOT),
        # yfinance가 쓰는 HTTP 라이브러리의 인증서·바이너리
        "--collect-all", "curl_cffi",
        "--collect-data", "certifi",
        # 쓰지 않는 큰 모듈은 빼서 용량을 줄인다
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        "--exclude-module", "PIL",    # 아이콘을 만들 때만 쓰는 빌드 도구라 묶음에 넣지 않는다
        str(ROOT / "packaging" / "run_app.py"),
    ]
    print(" ".join(command))
    code = subprocess.call(command, cwd=ROOT)
    if code != 0:
        return code
    # 배포용 zip과 무결성 확인용 SHA-256
    archive = shutil.make_archive(str(ROOT / "dist" / "MockTrading-windows"), "zip", ROOT / "dist", "MockTrading")
    digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    checksum = Path(archive).with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {Path(archive).name}\n", encoding="ascii")
    print(f"{archive}\nSHA-256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
