"""의존성 잠금 파일이 직접 의존성 파일과 맞는지(고치고 lock을 다시 안 만들면 여기서 걸린다)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKS = {
    "requirements.lock": ["requirements.txt"],
    "requirements-py311.lock": ["requirements.txt"],
    "requirements-build.lock": ["requirements.txt", "requirements-build.txt"],
}
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\#]+)")


def pins(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line.strip())
        if match:
            out[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return out


def test_every_direct_pin_is_in_its_lock_with_the_same_version():
    for lock, sources in LOCKS.items():
        locked = pins(ROOT / lock)
        for source in sources:
            for name, version in pins(ROOT / source).items():
                assert locked.get(name) == version, f"{lock}: {name}=={version} (다시 만들기: python packaging/lock.py)"


def test_every_locked_package_has_a_hash():
    for lock in LOCKS:
        text = (ROOT / lock).read_text(encoding="utf-8")
        blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
        for block in blocks:
            if PIN.match(block.strip()):
                assert "--hash=sha256:" in block, f"{lock}: 해시 없음 → {block.splitlines()[0]}"


def test_app_and_build_locks_agree_on_shared_packages():
    """Mock_QA DL2: CI 3.14는 빌드 lock으로 검사하므로, 사용자가 설치하는 앱 lock과 공통 패키지 버전이 같아야
    그 검사가 사용자 설치 버전을 검사한 것이 된다(lock.py로 둘을 함께 만들면 같다)."""
    app, build = pins(ROOT / "requirements.lock"), pins(ROOT / "requirements-build.lock")
    different = {name: (app[name], build[name]) for name in app.keys() & build.keys() if app[name] != build[name]}
    assert different == {}, f"앱 lock과 빌드 lock 버전이 다름: {different} (다시 만들기: python packaging/lock.py)"
    assert app.keys() <= build.keys()


def test_build_refuses_an_environment_that_differs_from_the_lock(tmp_path):
    """Mock_Sequrity Info: 빌드 환경 버전이 lock과 다르면 build.py가 멈춘다(없거나 다른 버전을 찾아낸다)."""
    import sys

    sys.path.insert(0, str(ROOT / "packaging"))
    import build

    import pytest as pytest_module

    lock = tmp_path / "x.lock"
    lock.write_text(
        f"pytest=={pytest_module.__version__} \\n    --hash=sha256:00\n"
        "pytest==0.0.1 \\n    --hash=sha256:00\n"
        "no-such-package-xyz==1.0 \\n    --hash=sha256:00\n",
        encoding="utf-8",
    )
    problems = build.lock_mismatches(lock)
    assert problems == ["pytest: lock 0.0.1, 설치됨 " + pytest_module.__version__, "no-such-package-xyz: lock 1.0, 설치됨 없음"]
