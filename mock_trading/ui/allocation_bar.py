"""자산 구성 막대: 총자산 100%를 한 줄 가로 막대로 나누고, 아래에 이름·비중·금액 범례를 둔다.

비중(전체 중 부분)은 도넛보다 한 줄 막대가 칸 크기를 비교하기 쉽다. 색은 종목을 따라가고(allocation.slices),
글자는 색을 입히지 않는다(옆의 색 견본이 종목을 알려 준다). 칸 위에 마우스를 올리면 툴팁으로 값을 보여 준다.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from ..allocation import Slice
from . import format as fmt
from .theme import SERIES_COLORS, SLICE_CASH, SLICE_OTHER

BAR_HEIGHT = 14
GAP = 2       # 칸 사이 바탕색 틈
RADIUS = 4    # 막대 양 끝 둥글기
LEGEND_COLUMNS = 2


def slice_color(item: Slice) -> QColor:
    if item.slot is not None:
        return QColor(SERIES_COLORS[item.slot % len(SERIES_COLORS)])
    return QColor(SLICE_CASH if item.key == "현금" else SLICE_OTHER)


def describe(item: Slice) -> str:
    return f"{item.label} {item.share}% · {fmt.won(item.value)}"


class AllocationBar(QWidget):
    """한 줄 100% 막대. 칸마다 2px 틈, 양 끝만 둥글게."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.slices: list[Slice] = []
        self.setMinimumHeight(BAR_HEIGHT)
        self.setMaximumHeight(BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_slices(self, slices: list[Slice]) -> None:
        self.slices = slices
        self.setAccessibleDescription(", ".join(describe(s) for s in slices))
        self.update()

    def segments(self) -> list[tuple[Slice, QRectF]]:
        """칸별 사각형. 너무 작은 칸도 1px은 보이게 하고, 틈을 뺀 나머지를 비중대로 나눈다."""
        if not self.slices:
            return []
        width = self.width() - GAP * (len(self.slices) - 1)
        total = sum(float(s.value) for s in self.slices)
        out, x = [], 0.0
        for item in self.slices:
            w = max(1.0, width * float(item.value) / total)
            out.append((item, QRectF(x, 0, w, BAR_HEIGHT)))
            x += w + GAP
        return out

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름 규칙)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        segments = self.segments()
        if not segments:
            painter.setBrush(QColor(SLICE_CASH))
            painter.drawRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
            return
        # 전체를 둥근 모양으로 자른 뒤 칸을 채운다 → 양 끝만 둥글고 가운데 칸은 반듯하다
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, self.width(), BAR_HEIGHT), RADIUS, RADIUS)
        painter.setClipPath(clip)
        for item, rect in segments:
            painter.setBrush(slice_color(item))
            painter.drawRect(rect)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt 이름 규칙)
        for item, rect in self.segments():
            if rect.left() - GAP <= event.position().x() <= rect.right() + GAP:
                # 툴팁은 HTML로 해석될 수 있어 종목명을 이스케이프한다
                text = f"<p style='white-space:pre'>{html.escape(describe(item))}</p>"
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()


class _Swatch(QWidget):
    def __init__(self, color: QColor) -> None:
        super().__init__()
        self.color = color
        self.setFixedSize(10, 10)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름 규칙)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)
        painter.drawRoundedRect(QRectF(self.rect()), 2, 2)


class AllocationPanel(QWidget):
    """막대 + 범례(색 견본, 이름, 비중, 금액). 범례가 막대의 글자 라벨 역할을 한다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.bar = AllocationBar()
        layout.addWidget(self.bar)
        self.legend = QGridLayout()
        self.legend.setHorizontalSpacing(24)
        self.legend.setVerticalSpacing(6)
        layout.addLayout(self.legend)
        self.empty = QLabel("보유 종목과 현금이 없어요")
        self.empty.setTextFormat(Qt.PlainText)
        self.empty.setObjectName("caption")
        layout.addWidget(self.empty)
        self.legend_labels: list[QLabel] = []

    def set_slices(self, slices: list[Slice]) -> None:
        self.bar.set_slices(slices)
        while self.legend.count():
            widget = self.legend.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self.legend_labels = []
        for index, item in enumerate(slices):
            row, column = divmod(index, LEGEND_COLUMNS)
            cell = QWidget()
            line = QHBoxLayout(cell)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            line.addWidget(_Swatch(slice_color(item)), 0, Qt.AlignVCenter)
            label = QLabel(describe(item))
            label.setTextFormat(Qt.PlainText)  # 종목명은 외부(시세 출처)에서 온 글자
            line.addWidget(label, 1)
            self.legend.addWidget(cell, row, column)
            self.legend_labels.append(label)
        self.empty.setVisible(not slices)


__all__ = ["AllocationBar", "AllocationPanel", "slice_color", "describe"]
