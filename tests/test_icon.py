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
