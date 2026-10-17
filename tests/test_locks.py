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
