"""도메인 모델과 금액 계산 규칙.

금액은 전부 Decimal로 다룬다. float는 0.1 + 0.2 != 0.3 같은 오차가 있어
잔고가 1원씩 어긋나는 문제를 만든다.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

KRW_UNIT = Decimal("1")
# 달러 가격은 센트(0.01)가 아니라 0.0001 단위로 둔다. 센트로 반올림하면 0.005달러 미만 종목이
# 0원에 체결되거나, 매수·매도 반올림 방향 차이로 차익이 생긴다.
USD_PRICE_UNIT = Decimal("0.0001")
FX_UNIT = Decimal("0.01")
MAX_NAME_LENGTH = 100

# 원/달러 환율이 이 범위를 벗어나면 데이터 오류로 본다.
USD_KRW_MIN = Decimal(500)
USD_KRW_MAX = Decimal(3000)

# 한 번에 주문할 수 있는 최대 수량. 비정상적으로 큰 입력이 계산을 망가뜨리지 않게 막는다.
MAX_ORDER_QUANTITY = 1_000_000


class Currency(str, Enum):
    KRW = "KRW"
    USD = "USD"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    OPEN = "OPEN"            # 체결 대기
    FILLED = "FILLED"        # 체결됨
    CANCELLED = "CANCELLED"  # 사용자가 취소
    REJECTED = "REJECTED"    # 조건은 맞았지만 현금·보유 수량 부족 등으로 체결하지 못함


class TradingError(Exception):
    """사용자에게 그대로 보여줄 수 있는 주문 오류."""


def to_decimal(value: float | int | str | Decimal) -> Decimal:
    """외부 값(yfinance float 등)을 Decimal로 바꾼다. float는 문자열을 거쳐 이진 오차를 없앤다.

    NaN·Infinity는 장부에 한 번 들어가면 이후 모든 계산을 망가뜨리므로 여기서 막는다.
    """
    try:
        if isinstance(value, bool):
            raise TypeError
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, float):
            result = Decimal(repr(value))
        else:
            result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise TradingError(f"숫자가 아닌 값입니다: {value!r}") from None
    if not result.is_finite():
        raise TradingError(f"올바르지 않은 숫자입니다: {value!r}")
    return result


def positive_decimal(value: float | int | str | Decimal, what: str) -> Decimal:
    result = to_decimal(value)
    if result <= 0:
        raise TradingError(f"{what} 값이 올바르지 않습니다: {value!r}")
    return result


def krw(value: Decimal) -> Decimal:
    """원화는 1원 단위로 반올림."""
    return value.quantize(KRW_UNIT, rounding=ROUND_HALF_UP)


def krw_floor(value: Decimal) -> Decimal:
    """수수료처럼 사용자에게 불리하지 않게 버림해야 하는 원화 금액."""
    return value.quantize(KRW_UNIT, rounding=ROUND_DOWN)


def normalize_price(currency: Currency, value: Decimal) -> Decimal:
    """시세를 통화별 단위로 맞춘다. 체결과 평가가 같은 가격을 쓰도록 시세를 받을 때 한 번만 적용한다."""
    unit = KRW_UNIT if currency is Currency.KRW else USD_PRICE_UNIT
    price = value.quantize(unit, rounding=ROUND_HALF_UP)
    if price <= 0:
        raise TradingError("가격이 너무 낮아 거래할 수 없는 종목입니다.")
    return price


def normalize_fx(value: Decimal) -> Decimal:
    rate = value.quantize(FX_UNIT, rounding=ROUND_HALF_UP)
    if rate <= 0:
        raise TradingError("환율 값이 올바르지 않습니다.")
    return rate


def clean_name(value: object, fallback: str) -> str:
    """외부에서 받은 종목명: 문자열만, 제어문자 제거, 길이 제한."""
    if not isinstance(value, str):
        return fallback
    text = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C").strip()
    return text[:MAX_NAME_LENGTH] or fallback


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
class LimitOrder:
    """지정가 주문. 매수는 현재가가 지정가 이하, 매도는 이상이 되면(두 번 연속 확인) 지정가로 체결한다."""
    id: int
    created_at: datetime
    symbol: str
    name: str
    side: Side
    quantity: int
    limit_price: Decimal  # 종목 통화 기준
    currency: Currency
    status: OrderStatus
    trade_id: int | None = None
    note: str = ""
    fx_at_order: Decimal | None = None  # 미국 주식: 주문할 때의 환율(체결 때 크게 다르면 보류)
    armed: bool = False                 # 직전 확인에서 이미 조건이 맞았는지(두 번 연속 맞아야 체결)
    armed_at: datetime | None = None    # 처음 조건이 맞은 시각(두 번째 확인은 최소 간격 뒤에만)

    def is_triggered(self, price: Decimal) -> bool:
        return price <= self.limit_price if self.side is Side.BUY else price >= self.limit_price


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
    """현재가를 반영한 보유 종목 평가. 시세를 못 가져오면 quote가 None이고 원가로 평가한다."""
    holding: Holding
    quote: Quote | None
    fx_rate: Decimal | None

    @property
    def has_price(self) -> bool:
        return self.quote is not None and self.fx_rate is not None

    @property
    def value_krw(self) -> Decimal:
        if not self.has_price:
            return self.holding.cost_krw
        return krw(self.quote.price * self.fx_rate * self.holding.quantity)

    @property
    def profit_krw(self) -> Decimal:
        return self.value_krw - self.holding.cost_krw

    @property
    def profit_rate(self) -> Decimal:
        if not self.holding.cost_krw:
            return Decimal(0)
        return self.profit_krw / self.holding.cost_krw * 100
