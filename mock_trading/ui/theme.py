"""앱 전체 모양. 연한 회색 바탕 위에 흰 둥근 카드, 큰 숫자, 파란 포인트 하나.

색은 format.py의 상승(빨강)·하락(파랑)과 맞춘다.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import QApplication

BACKGROUND = "#F2F4F6"
CARD = "#FFFFFF"
TEXT = "#191F28"
TEXT_SUB = "#5F6B7A"   # 흰 바탕·회색 바탕(#F2F4F6) 모두 대비 4.5:1 이상
LINE = "#E5E8EB"
ACCENT = "#1E5FD9"
ACCENT_SOFT = "#E8EEFB"

# 설치돼 있으면 쓰고, 없으면 윈도우 기본 한글 글꼴로
FONT_FAMILIES = ["Pretendard", "IBM Plex Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", "sans-serif"]

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-size: 14px;
}}
QMainWindow, QMainWindow > QWidget {{
    background: {BACKGROUND};
}}
QGroupBox {{
    background: {CARD};
    border: none;
    border-radius: 20px;
    margin-top: 0;
    padding: 46px 18px 14px 18px;
    font-size: 16px;
    font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 20px;
    top: 16px;
    color: {TEXT};
}}
QGroupBox#flat {{ background: transparent; padding: 38px 0 0 0; }}
QGroupBox#flat::title {{ left: 0; top: 8px; }}
QWidget#card {{ background: {CARD}; border-radius: 20px; }}
QLabel#brand {{ font-size: 19px; font-weight: 800; padding: 6px 12px; }}
QPushButton#navButton {{
    background: transparent;
    text-align: left;
    padding: 11px 14px;
    border-radius: 12px;
    color: #4E5968;
    font-size: 15px;
    font-weight: 700;
}}
QPushButton#navButton:hover {{ background: {LINE}; color: {TEXT}; }}
QPushButton#navButton:checked {{ background: {CARD}; color: {TEXT}; }}
QPushButton#navButton:focus {{ border: 2px solid {ACCENT}; }}
QPushButton {{
    background: {BACKGROUND};
    border: 2px solid transparent;
    border-radius: 12px;
    padding: 9px 16px;
    min-height: 24px;
    font-weight: 600;
    color: #333D4B;
}}
QPushButton:hover {{ background: {LINE}; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; }}
/* A3: 흰 대화상자 위 버튼은 테두리로 모양을 보이게, 기본 버튼(Enter)은 파랗게 */
QDialog QPushButton {{ border: 1px solid #A8B0B9; background: {CARD}; }}
QDialog QPushButton:focus {{ border: 2px solid {ACCENT}; }}
QDialog QPushButton:default {{ background: {ACCENT}; color: #FFFFFF; border: 2px solid {ACCENT}; }}
QDialog QPushButton:default:focus {{ border: 2px solid {TEXT}; }}
QPushButton:disabled {{ color: #B0B8C1; }}
QPushButton:checked {{ background: #FFF4E5; color: #B86100; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BACKGROUND};
    border: 2px solid transparent;
    border-radius: 12px;
    padding: 7px 12px;
    min-height: 24px;
    font-weight: 600;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {ACCENT};
    background: {CARD};
}}
QLineEdit:disabled, QDoubleSpinBox:disabled {{ color: #B0B8C1; }}
QSpinBox, QDoubleSpinBox {{ padding: 0 12px; min-height: 40px; }}
/* 숫자 칸 안에도 QLineEdit가 들어 있어 위의 입력칸 모양이 한 번 더 입혀지면 글씨가 밀려 사라진다 */
QAbstractSpinBox QLineEdit {{ background: transparent; border: none; padding: 0; min-height: 0; }}
/* 화살표는 숨긴다. setButtonSymbols(NoButtons)는 스타일시트와 함께 쓰면 안쪽 입력칸 폭이 1px로 계산되는
   Qt 문제가 있어, 버튼은 둔 채 폭을 0으로 만든다(키보드 위아래·휠은 그대로 동작). */
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: 8px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QTabWidget::pane {{
    background: {CARD};
    border: none;
    border-radius: 20px;
    padding: 8px;
}}
QTabBar::tab {{
    background: transparent;
    border: 2px solid transparent;
    color: {TEXT_SUB};
    padding: 10px 18px;
    margin: 0 4px 8px 0;
    border-radius: 12px;
    font-weight: 700;
}}
QTabBar::tab:selected {{ background: {CARD}; color: {TEXT}; }}
QTabBar::tab:hover:!selected {{ background: {LINE}; }}
QTabBar::tab:focus {{ border: 2px solid {ACCENT}; }}
QTableWidget {{
    background: {CARD};
    border: 2px solid transparent;
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    outline: none;
}}
QTableWidget:focus {{ border: 2px solid {ACCENT}; }}
QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid #F2F4F6; }}
QHeaderView::section {{
    background: {CARD};
    border: none;
    border-bottom: 1px solid {LINE};
    padding: 10px;
    color: {TEXT_SUB};
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {CARD}; border: none; }}
QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px; }}
QScrollBar::handle:vertical {{ background: #D1D6DB; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QChartView {{ background: {CARD}; border-radius: 20px; }}
QMessageBox, QInputDialog {{ background: {CARD}; }}
QLabel#caption {{ color: {TEXT_SUB}; font-size: 13px; font-weight: 500; }}
QLabel#bigNumber {{ font-size: 22px; font-weight: 700; }}
QLabel#heroNumber {{ font-size: 32px; font-weight: 800; }}
QLabel#status {{ color: {TEXT_SUB}; font-size: 12px; padding: 2px 6px; }}
"""


def apply_theme(app: QApplication) -> None:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)


def style_chart(chart) -> None:
    """QtCharts 기본 회색 테두리·배경 대신 카드와 같은 모양."""
    chart.setBackgroundBrush(QColor(CARD))
    chart.setBackgroundRoundness(20)
    chart.setMargins(chart.margins().__class__(8, 8, 8, 8))
    title_font = QFont()
    title_font.setFamilies(FONT_FAMILIES)
    title_font.setPointSize(11)
    title_font.setBold(True)
    chart.setTitleFont(title_font)
    chart.setTitleBrush(QColor(TEXT))


def style_axes(chart) -> None:
    """축 글씨는 보조색, 격자는 아주 연하게."""
    for axis in chart.axes():
        axis.setLabelsColor(QColor(TEXT_SUB))
        axis.setGridLineColor(QColor(LINE))
        axis.setLinePen(QPen(QColor(LINE)))
        axis.setTitleBrush(QColor(TEXT_SUB))
