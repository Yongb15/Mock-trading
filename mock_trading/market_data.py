"""실제 주식 시세 조회.

QuoteProvider 인터페이스 뒤에 yfinance를 숨겨, 테스트에서는 가짜 시세를 넣고
나중에 한국투자증권 API 같은 다른 출처로 바꿀 수 있게 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .domain import Currency, Quote, TradingError, to_decimal

USD_KRW_SYMBOL = "KRW=X"

# 허용하는 종목 코드 형태. 임의 문자열이 외부 API 요청으로 그대로 나가지 않게 한다.
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
_KR_CODE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class PricePoint:
    at: datetime
    close: Decimal


class QuoteProvider(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...
    def get_usd_krw(self) -> Decimal: ...
    def get_history(self, symbol: str, period: str = "3mo") -> list[PricePoint]: ...


def normalize_symbol(raw: str) -> str:
    """입력 정리: 공백 제거, 대문자. 6자리 숫자는 한국 종목 코드로 본다(시장 접미사는 조회 시 결정)."""
    symbol = raw.strip().upper()
    if not _SYMBOL_PATTERN.match(symbol):
        raise TradingError(f"올바른 종목 코드가 아닙니다: {raw!r}")
    return symbol


class YFinanceProvider:
    """yfinance 기반 시세. 무료라 약 15분 지연될 수 있다."""

    def __init__(self) -> None:
        import yfinance  # 테스트에서 네트워크 라이브러리를 불러오지 않도록 지연 import

        self._yf = yfinance

    def _resolve(self, symbol: str) -> tuple[str, object]:
        """6자리 한국 코드는 코스피(.KS) → 코스닥(.KQ) 순서로 찾아본다."""
        symbol = normalize_symbol(symbol)
        candidates = [f"{symbol}.KS", f"{symbol}.KQ"] if _KR_CODE.match(symbol) else [symbol]
        for candidate in candidates:
            ticker = self._yf.Ticker(candidate)
            try:
                price = ticker.fast_info.last_price
            except Exception:
                continue
            if price and price > 0:
                return candidate, ticker
        raise TradingError(f"시세를 찾을 수 없는 종목입니다: {symbol}")

    def get_quote(self, symbol: str) -> Quote:
        resolved, ticker = self._resolve(symbol)
        fast = ticker.fast_info
        currency_code = (fast.currency or "").upper()
        try:
            currency = Currency(currency_code)
        except ValueError:
            raise TradingError(f"지원하지 않는 통화입니다: {currency_code or '알 수 없음'}") from None

        try:
            info = ticker.info
            name = info.get("shortName") or info.get("longName") or resolved
        except Exception:
            name = resolved

        previous = fast.previous_close
        return Quote(
            symbol=resolved,
            name=name,
            price=to_decimal(fast.last_price),
            currency=currency,
            previous_close=to_decimal(previous) if previous else None,
        )

    def get_usd_krw(self) -> Decimal:
        rate = self._yf.Ticker(USD_KRW_SYMBOL).fast_info.last_price
        if not rate or rate <= 0:
            raise TradingError("환율을 가져오지 못했습니다.")
        return to_decimal(rate)

    def get_history(self, symbol: str, period: str = "3mo") -> list[PricePoint]:
        resolved, ticker = self._resolve(symbol)
        frame = ticker.history(period=period)
        return [
            PricePoint(at=index.to_pydatetime(), close=to_decimal(float(close)))
            for index, close in frame["Close"].items()
        ]
