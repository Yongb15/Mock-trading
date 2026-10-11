from __future__ import annotations

from decimal import Decimal

from ..domain import Currency, krw

UP_COLOR = "#d6336c"    # 한국 증시 관례: 상승 빨강
DOWN_COLOR = "#1c7ed6"  # 하락 파랑


def won(value: Decimal) -> str:
    return f"{krw(value):,}원"


def signed_won(value: Decimal) -> str:
    value = krw(value)
    return f"{value:+,}원" if value else "0원"


def price(value: Decimal, currency: Currency) -> str:
    if currency is Currency.KRW:
        return f"{krw(value):,}원"
    return f"${value:,.2f}"


def percent(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def color_for(value: Decimal | None) -> str:
    if not value:
        return ""
    return UP_COLOR if value > 0 else DOWN_COLOR
