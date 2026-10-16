import csv
import os
from datetime import datetime
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mock_trading.domain import Currency, Side, Trade  # noqa: E402
from mock_trading.export import HEADER, trade_rows, write_trades_csv  # noqa: E402


def trade(name="삼성전자", symbol="005930.KS", side=Side.BUY):
    return Trade(1, datetime(2026, 10, 16, 9, 30), symbol, name, side, 3, Decimal("70000"), Currency.KRW,
                 Decimal("1"), Decimal("210000"), Decimal("31"))


def test_csv_opens_in_excel_with_korean(tmp_path):
    path = tmp_path / "out"  # 확장자가 없으면 .csv를 붙인다
    assert write_trades_csv([trade(), trade(side=Side.SELL)], path) == 2
    raw = (tmp_path / "out.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM → 엑셀이 UTF-8로 읽는다
    with (tmp_path / "out.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == HEADER
    assert rows[1] == ["2026-10-16 09:30:00", "매수", "삼성전자", "005930.KS", "3", "70000", "KRW", "1", "210000", "31"]
    assert rows[2][1] == "매도"


def test_formula_injection_is_neutralized():
    """외부에서 온 종목명이 =, +, -, @로 시작하면 엑셀이 수식으로 실행하지 않게 '를 붙인다."""
    for evil in ['=HYPERLINK("http://x","click")', "+1+1", "-2+3", "@SUM(A1)", "\t=1"]:
        [row] = trade_rows([trade(name=evil)])
        assert row[2].startswith("'") and row[2][1:] == evil
    [row] = trade_rows([trade(name="Apple")])
    assert row[2] == "Apple"


def test_export_button_writes_file(tmp_path):
    from PySide6.QtWidgets import QApplication, QFileDialog

    from mock_trading.broker import Broker
    from mock_trading.storage import Repository
    from mock_trading.ui.main_window import MainWindow

    from .test_broker import FakeProvider

    app = QApplication.instance() or QApplication([])
    broker = Broker(Repository(check_same_thread=False), FakeProvider())
    broker.open_account(10_000_000)
    broker.buy("005930", 2, expected_price=Decimal("70000"))
    window = MainWindow(broker)
    target = tmp_path / "trades.csv"
    original = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(target), "CSV 파일 (*.csv)"))
    try:
        window.export_trades()
        window.reader.wait(3000)
        app.processEvents()
    finally:
        QFileDialog.getSaveFileName = original
    assert target.exists()
    assert "거래 1건을 내보냈습니다" in window.status.text()
    window.close()
