"""실제 주식 시세 조회.

QuoteProvider 인터페이스 뒤에 yfinance를 숨겨, 테스트에서는 가짜 시세를 넣고
나중에 한국투자증권 API 같은 다른 출처로 바꿀 수 있게 한다.

외부 응답은 믿지 않는다: 숫자는 유한한 양수인지, 종목명은 문자열인지 확인한 뒤에만 쓴다.
"""
from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from .domain import (
    USD_KRW_MAX,
    USD_KRW_MIN,
    Currency,
    Quote,
    TradingError,
    clean_name,
    normalize_fx,
    normalize_price,
    positive_decimal,
)

USD_KRW_SYMBOL = "KRW=X"

class DownloadBusy(Exception):
    """다른 일괄 조회가 진행 중이라 이번엔 조회하지 않았다(실패가 아니다 — 다음에 다시 하면 된다)."""


# yf.download는 모듈 전역 상태를 쓰므로 두 스레드에서 동시에 부르면 결과가 섞일 수 있다 → 한 번에 하나씩.
_DOWNLOAD_LOCK = threading.Lock()

# 일괄 조회 때 동시에 보내는 요청 수 상한. 너무 많이 몰리면 Yahoo가 요청을 제한(429)해 주문용 시세까지 실패할 수 있다.
DOWNLOAD_THREADS = 4


@contextmanager
def _quiet_batch_failures(symbols: list[str]):
    """일괄 조회에서 yfinance는 실패한 종목을 stderr에 찍는다(종목별 오류는 yfinance 내부 스레드에서).
    실패는 결과에서 빠지는 것으로 드러나므로, 이 구간 동안 이 스레드의 요약 메시지와 **이번 조회 종목**이 들어간
    메시지만 거른다. 레벨을 바꾸지 않으므로 다른 종목·다른 스레드(주문 경로) 로그는 그대로 남는다."""
    logger = logging.getLogger("yfinance")
    me = threading.get_ident()
    # 부분 문자열로 비교하면 "T"·"A" 같은 한 글자 종목 때문에 거의 모든 로그가 숨겨진다 → 코드 앞뒤 경계를 본다
    pattern = re.compile(
        "|".join(rf"(?<![A-Z0-9.]){re.escape(name)}(?![A-Z0-9])" for name in symbols)
    ) if symbols else None

    def keep(record: logging.LogRecord) -> bool:
        if record.thread == me:
            return False
        return pattern is None or pattern.search(record.getMessage()) is None

    logger.addFilter(keep)
    try:
        yield
    finally:
        logger.removeFilter(keep)


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


def is_valid_symbol(value: object) -> bool:
    return isinstance(value, str) and _SYMBOL_PATTERN.fullmatch(value) is not None


def symbol_candidates(raw: object) -> list[str]:
    """6자리 한국 코드는 저장될 수 있는 두 형태(.KS/.KQ)를, 그 밖에는 정규화한 코드 하나를 돌려준다."""
    symbol = normalize_symbol(raw)
    return [f"{symbol}.KS", f"{symbol}.KQ"] if _KR_CODE.fullmatch(symbol) else [symbol]


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

    def get_quotes(self, items: list[tuple[str, str, Currency]], *, wait: float | None = None) -> dict[str, Quote]:
        """관심 종목처럼 여러 종목을 한 번의 호출로 조회한다. 실패한 종목은 결과에서 빠진다.

        yfinance는 내부적으로 종목마다 요청을 보내므로(최대 DOWNLOAD_THREADS개씩 병렬) 종목 수만큼 요청이 나간다.
        종목별 fast_info + info 조회보다는 훨씬 가볍다.
        items: (정규화된 코드, 이름, 통화). 일봉 종가의 마지막 두 값을 현재가·전일 종가로 쓴다.
        wait: 다른 일괄 조회가 진행 중일 때 기다릴 최대 초. 넘으면 DownloadBusy(주문 대기열에서 오래 멈추지 않게).
        """
        valid = []
        for symbol, name, currency in items:
            try:  # 손상된 코드 하나가 나머지 종목 조회까지 막지 않게 종목별로 확인
                valid.append((normalize_symbol(symbol), name, currency))
            except TradingError:
                continue
        items = valid
        symbols = [symbol for symbol, _, _ in items]
        if not symbols:
            return {}
        if not _DOWNLOAD_LOCK.acquire(timeout=-1 if wait is None else wait):
            raise DownloadBusy()
        try:
            with _quiet_batch_failures(symbols):
                frame = self._yf.download(
                    symbols, period="5d", progress=False, auto_adjust=False, threads=DOWNLOAD_THREADS
                )
        finally:
            _DOWNLOAD_LOCK.release()
        closes = frame["Close"]
        quotes = {}
        for symbol, name, currency in items:
            try:
                series = closes[symbol] if hasattr(closes, "columns") else closes
                values = [positive_decimal(float(v), "종가") for v in series.dropna().tolist()[-2:]]
                price = normalize_price(currency, values[-1])
                previous = normalize_price(currency, values[-2]) if len(values) == 2 else None
            except Exception:
                continue
            quotes[symbol] = Quote(symbol, clean_name(name, symbol), price, currency, previous)
        return quotes

    def get_daily_closes(
        self, symbols: list[str], start: date, *, include_usd_krw: bool
    ) -> tuple[dict[str, dict[date, Decimal]], dict[date, Decimal]]:
        """여러 종목(과 원/달러 환율)의 start 이후 일별 종가를 요청 한 번으로 받는다. 비정상 값은 버린다."""
        tickers = [normalize_symbol(s) for s in symbols]
        if include_usd_krw:
            tickers.append(USD_KRW_SYMBOL)
        if not tickers:
            return {}, {}
        with _DOWNLOAD_LOCK, _quiet_batch_failures(tickers):
            frame = self._yf.download(
                tickers, start=start.isoformat(), progress=False, auto_adjust=False, threads=DOWNLOAD_THREADS
            )
        closes_frame = frame["Close"]
        result: dict[str, dict[date, Decimal]] = {}
        for ticker in tickers:
            try:
                series = closes_frame[ticker] if hasattr(closes_frame, "columns") else closes_frame
            except KeyError:
                continue
            daily: dict[date, Decimal] = {}
            for index, value in series.dropna().items():
                try:
                    daily[index.date()] = positive_decimal(float(value), "종가")
                except TradingError:
                    continue
            result[ticker] = daily
        usd_krw = {}
        for day, value in result.pop(USD_KRW_SYMBOL, {}).items():
            rate = normalize_fx(value)
            if USD_KRW_MIN <= rate <= USD_KRW_MAX:  # 주문 경로와 같은 범위로 이상값을 버린다
                usd_krw[day] = rate
        return result, usd_krw

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
