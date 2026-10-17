"""앱 아이콘: 창·작업 표시줄·exe가 같은 그림(theme.draw_icon)을 쓴다."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mock_trading.ui.theme import ICON_SIZES, app_icon, draw_icon  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_icon_has_every_size_with_transparent_corners(app):
    for size in ICON_SIZES:
        image = draw_icon(size).toImage()
        assert (image.width(), image.height()) == (size, size)
        assert image.pixelColor(0, 0).alpha() == 0  # 둥근 모서리 바깥은 투명
        assert image.pixelColor(size // 2, size - 2).alpha() == 255  # 바탕은 불투명
    assert sorted(s.width() for s in app_icon().availableSizes()) == list(ICON_SIZES)


def test_build_writes_multi_size_ico(app, tmp_path):
    """exe 아이콘(.ico)에 모든 크기가 들어간다(작은 크기는 축소본이 아니라 전용 그림)."""
    pytest.importorskip("PIL")
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import build
    from PIL import Image

    path = tmp_path / "app.ico"
    build.make_icon(path)
    assert sorted(Image.open(path).info["sizes"]) == [(s, s) for s in ICON_SIZES]


def test_taskbar_app_id_only_for_source_runs(monkeypatch):
    """Mock_QA I1: 실행 파일에서는 앱 ID를 설정하지 않는다(이전 버전 고정 아이콘과 그룹이 갈리지 않게)."""
    import ctypes
    import sys

    from mock_trading import app as app_module

    if sys.platform != "win32":
        pytest.skip("Windows 전용")
    calls = []

    class Shell:
        def SetCurrentProcessExplicitAppUserModelID(self, value):  # noqa: N802 (Windows API 이름)
            calls.append(value)

    monkeypatch.setattr(ctypes, "windll", type("W", (), {"shell32": Shell()})(), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    app_module._use_own_taskbar_icon()
    assert calls == []
    monkeypatch.delattr(sys, "frozen")
    app_module._use_own_taskbar_icon()
    assert calls == ["Yongb15.MockTrading"]
