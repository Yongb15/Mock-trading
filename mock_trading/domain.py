"""도메인 모델과 금액 계산 규칙.

금액은 전부 Decimal로 다룬다. float는 0.1 + 0.2 != 0.3 같은 오차가 있어
잔고가 1원씩 어긋나는 문제를 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import Enum

KRW_UNIT = Decimal("1")
USD_UNIT = Decimal("0.01")
FX_UNIT = Decimal("0.0001")

# 한 번에 주문할 수 있는 최대 수량. 비정상적으로 큰 입력이 계산을 망가뜨리지 않게 막는다.
MAX_ORDER_QUANTITY = 1_000_000


class Currency(str, Enum):
    KRW = "KRW"
    USD = "USD"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradingError(Exception):
    """사용자에게 그대로 보여줄 수 있는 주문 오류."""


def to_decimal(value: float | int | str | Decimal) -> Decimal:
    """외부 값(yfinance float 등)을 Decimal로 바꾼다. float는 문자열을 거쳐 이진 오차를 없앤다."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def krw(value: Decimal) -> Decimal:
    """원화는 1원 단위로 반올림."""
    return value.quantize(KRW_UNIT, rounding=ROUND_HALF_UP)


def krw_floor(value: Decimal) -> Decimal:
    """수수료처럼 사용자에게 불리하지 않게 버림해야 하는 원화 금액."""
    return value.quantize(KRW_UNIT, rounding=ROUND_DOWN)


def price_in(currency: Currency, value: Decimal) -> Decimal:
    unit = KRW_UNIT if currency is Currency.KRW else USD_UNIT
    return value.quantize(unit, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: Decimal
    currency: Currency
    previous_close: Decimal | None = None

    @property
    def change_rate(self) -> Decimal | None:
        if not self.previous_close:
            return None
        return (self.price - self.previous_close) / self.previous_close * 100


@dataclass(frozen=True)
class Trade:
    id: int
    executed_at: datetime
    symbol: str
    name: str
    side: Side
    quantity: int
    price: Decimal          # 체결 단가 (종목 통화 기준)
    currency: Currency
    fx_rate: Decimal        # 1 종목통화 = fx_rate 원. 원화 종목은 1
    amount_krw: Decimal     # 수수료 제외 체결 금액 (원)
    fee_krw: Decimal


@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str
    currency: Currency
    quantity: int
    cost_krw: Decimal       # 남은 수량의 매입 원가 합계 (수수료 포함, 원)

    @property
    def avg_cost_krw(self) -> Decimal:
        return krw(self.cost_krw / self.quantity) if self.quantity else Decimal(0)


@dataclass(frozen=True)
class Position:
    """현재가를 반영한 보유 종목 평가."""
    holding: Holding
    quote: Quote
    fx_rate: Decimal

    @property
    def value_krw(self) -> Decimal:
        return krw(self.quote.price * self.fx_rate * self.holding.quantity)

    @property
    def profit_krw(self) -> Decimal:
        return self.value_krw - self.holding.cost_krw

    @property
    def profit_rate(self) -> Decimal:
        if not self.holding.cost_krw:
            return Decimal(0)
        return self.profit_krw / self.holding.cost_krw * 100
