"""의존성 잠금 파일(.lock) 다시 만들기: python packaging/lock.py  (uv 필요 — https://docs.astral.sh/uv/)

requirements.txt·requirements-build.txt(직접 의존성, 사람이 고치는 파일)에서 간접 의존성까지 정확한 버전과
SHA-256 해시로 고정한 파일을 만든다. 설치는 `pip install --require-hashes -r <lock>`으로 해서, 받은 파일이
해시와 다르면 설치가 멈춘다(누가 패키지를 바꿔치기해도 리뷰한 것과 다른 코드가 실행되지 않게).

- requirements.lock         앱 실행용(Python 3.14, Windows)
- requirements-py311.lock   지원 하한 Python 3.11 검사용(CI)
- requirements-build.lock   실행 파일 빌드·README 화면용(Python 3.14, 앱 의존성 + 빌드 도구). CI 3.14도 이걸 쓴다.

직접 의존성 버전을 바꿨으면 이 스크립트로 셋을 함께 다시 만들고, 바뀐 lock도 리뷰를 받는다.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    ("requirements.lock", ["requirements.txt"], "3.14"),
    ("requirements-py311.lock", ["requirements.txt"], "3.11"),
    ("requirements-build.lock", ["requirements.txt", "requirements-build.txt"], "3.14"),
]


def main() -> int:
    for output, inputs, python in TARGETS:
        command = ["uv", "pip", "compile", *inputs, "--python-version", python, "--python-platform", "windows",
                   "--generate-hashes", "-o", output]
        print(" ".join(command))
        code = subprocess.call(command, cwd=ROOT)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
