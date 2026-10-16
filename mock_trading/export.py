"""거래 내역 내보내기(CSV).

엑셀에서 바로 열 수 있게 UTF-8(BOM 포함)로 쓰고, 셀 값이 =, +, -, @ 등으로 시작하면 앞에 '를 붙여
엑셀이 수식으로 실행하지 않게 한다(CSV 수식 주입 방지). 종목명은 외부(시세 출처)에서 온 값이라 특히 중요하다.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .domain import Side, Trade

HEADER = ["체결 시각", "구분", "종목명", "코드", "수량", "체결가", "통화", "환율", "체결 금액(원)", "수수료(원)"]
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_text(value: str) -> str:
    """사람이 읽는 글자 칸(종목명·코드 등)만 보호한다. 숫자 칸은 숫자 그대로 둔다(음수 -는 숫자의 일부).
    일부 스프레드시트는 앞 공백을 무시하고 수식으로 읽으므로 공백을 뗀 첫 글자로 판단한다."""
    return "'" + value if value.lstrip(" ").startswith(_FORMULA_PREFIXES) else value


def trade_rows(trades: list[Trade]) -> list[list[str]]:
    rows = []
    for t in trades:
        rows.append([
            t.executed_at.strftime("%Y-%m-%d %H:%M:%S"),
            "매수" if t.side is Side.BUY else "매도",
            _safe_text(t.name),
            _safe_text(t.symbol),
            str(t.quantity),
            str(t.price),
            t.currency.value,
            str(t.fx_rate),
            str(t.amount_krw),
            str(t.fee_krw),
        ])
    return rows


def write_trades_csv(trades: list[Trade], path: Path | str) -> int:
    """거래 내역을 사용자가 고른 경로 그대로에 CSV로 쓴다. 쓴 거래 수를 돌려준다.

    확장자를 여기서 바꾸면 저장 대화상자가 확인한 것과 다른 파일을 확인 없이 덮어쓸 수 있다 →
    .csv 붙이기는 대화상자(setDefaultSuffix)가 하고, 덮어쓰기 확인도 대화상자가 한다.
    """
    path = Path(path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(trade_rows(trades))
    return len(trades)
