from __future__ import annotations

from decimal import Decimal

from ..domain import Currency, krw

UP_COLOR = "#E02D3C"    # 한국 증시 관례: 상승 빨강
DOWN_COLOR = "#1E5FD9"  # 하락 파랑 (흰 바탕 대비 4.5:1 이상)


def won(value: Decimal) -> str:
    return f"{krw(value):,}원"


def signed_won(value: Decimal) -> str:
    value = krw(value)
    return f"{value:+,}원" if value else "0원"


def price(value: Decimal, currency: Currency) -> str:
    if currency is Currency.KRW:
        return f"{krw(value):,}원"
    # 보통은 센트까지, 1센트 미만 자리가 있는 저가주만 4자리까지 보여준다.
    places = 2 if value == value.quantize(Decimal("0.01")) else 4
    return f"${value:,.{places}f}"


def percent(value: Decimal | None) -> str:
    if value is None:
        return "-"
    rounded = value.quantize(Decimal("0.01"))
    if not rounded:
        return "0.00%"  # -0.00% 방지
    return f"{rounded:+.2f}%"


def color_for(value: Decimal | None) -> str:
    # 화면에 0.00%로 보이는 값은 색을 칠하지 않는다.
    if value is None or not value.quantize(Decimal("0.01")):
        return ""
    return UP_COLOR if value > 0 else DOWN_COLOR
