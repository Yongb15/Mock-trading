"""자산 구성: 총자산을 종목별 평가 금액과 현금으로 나눈 비중(내 계좌 화면의 가로 막대).

상위 몇 종목만 따로 보이고 나머지는 "기타"로 묶는다(색이 너무 많으면 구별이 안 된다).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .domain import Position

MAX_NAMED = 5  # 따로 보이는 종목 수(색 슬롯 수와 같다)


@dataclass(frozen=True)
class Slice:
    key: str            # 종목 코드, 또는 "기타"·"현금"
    label: str          # 화면에 보이는 이름
    value: Decimal      # 원화 금액
    share: Decimal      # 총자산 대비 비중(%)
    slot: int | None    # 색 슬롯(0부터). 기타·현금은 None(회색)


def slices(positions: list[Position], cash: Decimal, *, max_named: int = MAX_NAMED) -> list[Slice]:
    """큰 순서의 종목들 → 기타 → 현금. 금액이 0 이하인 칸은 뺀다.

    색은 순위가 아니라 종목을 따라간다: 보이는 종목끼리 처음 산 순서(positions 순서)로 슬롯을 준다.
    그래서 값이 오르내려 순서가 바뀌어도 같은 종목은 같은 색이다.
    """
    held = [p for p in positions if p.value_krw > 0]
    named = sorted(held, key=lambda p: p.value_krw, reverse=True)[:max_named]
    named_symbols = {p.holding.symbol for p in named}
    slot_of = {}
    for position in held:  # 처음 산 순서
        if position.holding.symbol in named_symbols:
            slot_of[position.holding.symbol] = len(slot_of)
    rest = sum((p.value_krw for p in held if p.holding.symbol not in named_symbols), Decimal(0))
    cash = max(cash, Decimal(0))
    total = sum((p.value_krw for p in held), Decimal(0)) + cash
    if total <= 0:
        return []

    def share(value: Decimal) -> Decimal:
        return (value / total * 100).quantize(Decimal("0.1"))

    out = [Slice(p.holding.symbol, p.holding.name, p.value_krw, share(p.value_krw), slot_of[p.holding.symbol])
           for p in named]
    if rest > 0:
        others = len(held) - len(named)
        out.append(Slice("기타", f"기타 {others}종목", rest, share(rest), None))
    if cash > 0:
        out.append(Slice("현금", "현금", cash, share(cash), None))
    return out
