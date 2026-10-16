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
    path = tmp_path / "out.csv"
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


def test_export_button_writes_to_the_chosen_path(tmp_path):
    """Mock_QA C1 / Mock_Sequrity CE1: 고른 경로 그대로에 쓰고, 상태 줄도 그 경로를 보여 준다."""
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
    target = tmp_path / "거래.2026.10"           # 점이 있는 이름도 확장자를 바꾸지 않는다
    seen = {}

    def fake_run(dialog):
        seen["suffix"] = dialog.defaultSuffix()
        seen["mode"] = dialog.acceptMode()
        dialog.selectFile(str(target))
        return True

    window._run_save_dialog = fake_run
    window.export_trades()
    window.reader.wait(3000)
    app.processEvents()
    assert seen == {"suffix": "csv", "mode": QFileDialog.AcceptSave}
    assert target.exists() and not (tmp_path / "거래.2026.csv").exists()
    assert "거래 1건을 내보냈습니다" in window.status.text() and "거래.2026.10" in window.status.text()

    window._run_save_dialog = lambda dialog: False   # 취소하면 아무것도 쓰지 않는다
    window.export_trades()
    window.close()


def test_write_does_not_touch_another_file(tmp_path):
    """예전에는 t.xlsx를 고르면 확인 없이 t.csv를 덮어썼다."""
    existing = tmp_path / "t.csv"
    existing.write_text("소중한 내용", encoding="utf-8")
    write_trades_csv([trade()], tmp_path / "t.xlsx")
    assert existing.read_text(encoding="utf-8") == "소중한 내용"
    assert (tmp_path / "t.xlsx").exists()


def test_leading_space_formula_is_neutralized():
    """Mock_Sequrity CE2"""
    [row] = trade_rows([trade(name="  =1+1")])
    assert row[2] == "'  =1+1"
    [row] = trade_rows([trade(name="  Apple")])
    assert row[2] == "  Apple"


def test_unicode_space_before_formula_is_neutralized():
    """Mock_Sequrity Info: NBSP·전각 공백 뒤의 수식도 막는다."""
    for evil in [" =1+1", "　=1+1", " @SUM(A1)"]:
        [row] = trade_rows([trade(name=evil)])
        assert row[2] == "'" + evil
    [row] = trade_rows([trade(name="　삼성전자")])
    assert row[2] == "　삼성전자"


def test_invisible_format_char_before_formula_is_neutralized():
    """Mock_Sequrity Info: 제로폭 공백·BOM 뒤의 수식도 막는다(DB를 직접 고친 행 등 clean_name을 안 거친 값 대비)."""
    from mock_trading.export import _safe_text

    for value in ["\u200b=1+1", "\ufeff+1", "\u200b \u00a0@SUM(A1)"]:
        assert _safe_text(value) == "'" + value
    assert _safe_text("Apple\u200bInc") == "Apple\u200bInc"
