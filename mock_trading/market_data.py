"""실제 주식 시세 조회.

QuoteProvider 인터페이스 뒤에 yfinance를 숨겨, 테스트에서는 가짜 시세를 넣고
나중에 한국투자증권 API 같은 다른 출처로 바꿀 수 있게 한다.

외부 응답은 믿지 않는다: 숫자는 유한한 양수인지, 종목명은 문자열인지 확인한 뒤에만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .domain import (
    Currency,
    Quote,
    TradingError,
    clean_name,
    normalize_fx,
    normalize_price,
    positive_decimal,
)

USD_KRW_SYMBOL = "KRW=X"
HISTORY_PERIODS = frozenset({"1mo", "3mo", "6mo", "1y", "5y"})

# 허용하는 종목 코드 형태. 임의 문자열이 외부 API 요청으로 그대로 나가지 않게 한다.
_SYMBOL_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
_KR_CODE = re.compile(r"\d{6}")


@dataclass(frozen=True)
class PricePoint:
    at: datetime
    close: Decimal


class QuoteProvider(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...
    def get_usd_krw(self) -> Decimal: ...
    def get_history(self, symbol: str, period: str = "3mo") -> list[PricePoint]: ...


def normalize_symbol(raw: object) -> str:
    """입력 정리: 공백 제거, 대문자. 6자리 숫자는 한국 종목 코드로 본다(시장 접미사는 조회 시 결정)."""
    if not isinstance(raw, str):
        raise TradingError("종목 코드는 문자열이어야 합니다.")
    symbol = raw.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise TradingError(f"올바른 종목 코드가 아닙니다: {raw!r}")
    return symbol


class YFinanceProvider:
    """yfinance 기반 시세. 무료라 약 15분 지연될 수 있다."""

    def __init__(self) -> None:
        import yfinance  # 테스트에서 네트워크 라이브러리를 불러오지 않도록 지연 import

        self._yf = yfinance
        # ticker.info는 수 초 걸리는 호출이라 종목명은 한 번만 가져온다.
        self._names: dict[str, str] = {}

    def _resolve(self, symbol: str) -> tuple[str, object]:
        """6자리 한국 코드는 코스피(.KS) → 코스닥(.KQ) 순서로 찾아본다."""
        symbol = normalize_symbol(symbol)
        candidates = [f"{symbol}.KS", f"{symbol}.KQ"] if _KR_CODE.fullmatch(symbol) else [symbol]
        for candidate in candidates:
            ticker = self._yf.Ticker(candidate)
            try:
                positive_decimal(ticker.fast_info.last_price, "현재가")
            except Exception:
                continue
            return candidate, ticker
        raise TradingError(f"시세를 찾을 수 없는 종목입니다: {symbol}")

    def _name(self, symbol: str, ticker: object) -> str:
        if symbol not in self._names:
            try:
                info = ticker.info
                raw = info.get("shortName") or info.get("longName")
            except Exception:
                return symbol  # 실패는 캐시하지 않고 다음에 다시 시도
            self._names[symbol] = clean_name(raw, symbol)
        return self._names[symbol]

    def get_quote(self, symbol: str) -> Quote:
        resolved, ticker = self._resolve(symbol)
        fast = ticker.fast_info
        currency_code = str(fast.currency or "").upper()
        try:
            currency = Currency(currency_code)
        except ValueError:
            raise TradingError(f"지원하지 않는 통화입니다: {currency_code[:10] or '알 수 없음'}") from None

        try:
            previous = normalize_price(currency, positive_decimal(fast.previous_close, "전일 종가"))
        except Exception:
            previous = None  # 전일 종가는 표시용이라 없어도 주문은 가능

        return Quote(
            symbol=resolved,
            name=self._name(resolved, ticker),
            price=normalize_price(currency, positive_decimal(fast.last_price, "현재가")),
            currency=currency,
            previous_close=previous,
        )

    def get_usd_krw(self) -> Decimal:
        return normalize_fx(positive_decimal(self._yf.Ticker(USD_KRW_SYMBOL).fast_info.last_price, "환율"))

    def get_history(self, symbol: str, period: str = "3mo") -> list[PricePoint]:
        if period not in HISTORY_PERIODS:
            raise TradingError(f"지원하지 않는 기간입니다: {period!r}")
        _, ticker = self._resolve(symbol)
        frame = ticker.history(period=period)
        points = []
        for index, close in frame["Close"].dropna().items():
            try:
                points.append(PricePoint(at=index.to_pydatetime(), close=positive_decimal(float(close), "종가")))
            except TradingError:
                continue  # 비정상 값이 있는 날은 차트에서 뺀다
        return points
