"""자산 추이: 날짜별 총자산을 거래 장부와 과거 종가로 다시 계산한다.

앱을 켠 날만 기록하는 방식이 아니라, 장부(언제 무엇을 몇 주 샀는지)와 그날의 종가·환율로
계좌를 연 날부터 오늘까지 매일의 평가액을 만든다. 그래서 앱을 안 켠 날도 빈칸이 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .domain import Currency, Side, Trade, krw

# 한 번에 그릴 최대 기간. 장부가 아주 길어도 조회·계산량이 끝없이 커지지 않게 한다.
MAX_HISTORY_DAYS = 366 * 3


@dataclass(frozen=True)
class AssetPoint:
    day: date
    cash: Decimal
    stock_value: Decimal
    initial_cash: Decimal
    missing_price: bool = False  # 그날 종가를 알 수 없는 종목이 있어 마지막 체결가로 대신 평가했는지

    @property
    def total(self) -> Decimal:
        return self.cash + self.stock_value

    @property
    def return_rate(self) -> Decimal:
        return (self.total - self.initial_cash) / self.initial_cash * 100


# 종목(또는 환율)별 날짜 → 종가
DailyCloses = dict[str, dict[date, Decimal]]


def _forward_filled(closes: dict[date, Decimal], days: list[date]) -> dict[date, Decimal]:
    """휴장일에는 직전 거래일 종가를 쓴다. 첫 종가 이전 날짜는 비워 둔다."""
    filled: dict[date, Decimal] = {}
    last: Decimal | None = None
    known = sorted(closes)
    i = 0
    for day in days:
        while i < len(known) and known[i] <= day:
            last = closes[known[i]]
            i += 1
        if last is not None:
            filled[day] = last
    return filled


def asset_history(
    *,
    initial_cash: Decimal,
    trades: list[Trade],
    start: date,
    end: date,
    closes: DailyCloses,
    usd_krw: dict[date, Decimal],
) -> list[AssetPoint]:
    """start~end의 날짜별 자산. closes·usd_krw에 없는 날은 직전 값을 쓰고, 전혀 모르면 마지막 체결가로 평가한다."""
    if end < start:
        return []
    start = max(start, end - timedelta(days=MAX_HISTORY_DAYS))
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    filled = {symbol: _forward_filled(series, days) for symbol, series in closes.items()}
    fx = _forward_filled(usd_krw, days)

    trades = sorted(trades, key=lambda t: (t.executed_at, t.id))
    cash = initial_cash
    quantity: dict[str, int] = {}
    cost: dict[str, Decimal] = {}
    currency: dict[str, Currency] = {}
    last_price: dict[str, tuple[Decimal, Decimal]] = {}  # 장부에 남은 마지막 체결가와 환율
    points = []
    t = 0
    for day in days:
        # 그날 끝까지의 거래를 반영 (장부 계산과 같은 이동평균 규칙)
        while t < len(trades) and trades[t].executed_at.date() <= day:
            trade = trades[t]
            held = quantity.get(trade.symbol, 0)
            held_cost = cost.get(trade.symbol, Decimal(0))
            if trade.side is Side.BUY:
                cash -= trade.amount_krw + trade.fee_krw
                quantity[trade.symbol] = held + trade.quantity
                cost[trade.symbol] = held_cost + trade.amount_krw + trade.fee_krw
            else:
                cash += trade.amount_krw - trade.fee_krw
                remaining = held - trade.quantity
                quantity[trade.symbol] = remaining
                cost[trade.symbol] = krw(held_cost * remaining / held) if held else Decimal(0)
            currency[trade.symbol] = trade.currency
            last_price[trade.symbol] = (trade.price, trade.fx_rate)
            t += 1

        stock_value = Decimal(0)
        missing = False
        for symbol, qty in quantity.items():
            if qty <= 0:
                continue
            price = filled.get(symbol, {}).get(day)
            if currency[symbol] is Currency.KRW:
                rate = Decimal(1)
            else:
                rate = fx.get(day) or last_price[symbol][1]
            if price is None:
                # 그날 종가를 모르면(상장 직후·데이터 없음) 마지막 체결가로 대신한다.
                price, rate = last_price[symbol]
                missing = True
            stock_value += krw(price * rate * qty)
        points.append(AssetPoint(day, cash, stock_value, initial_cash, missing))
    return points


def load_asset_history(repo, provider, today: date) -> list[AssetPoint]:
    """계좌를 연 날부터 오늘까지. 필요한 종목·환율의 과거 종가는 요청 한 번으로 받는다."""
    start = repo.account_opened_on()
    trades = repo.trades()
    symbols = sorted({t.symbol for t in trades})
    has_usd = any(t.currency is Currency.USD for t in trades)
    closes: DailyCloses = {}
    usd_krw: dict[date, Decimal] = {}
    get_daily_closes = getattr(provider, "get_daily_closes", None)
    if symbols and get_daily_closes is not None:
        try:
            closes, usd_krw = get_daily_closes(symbols, start, include_usd_krw=has_usd)
        except Exception:
            closes, usd_krw = {}, {}  # 과거 시세를 못 받아도 체결가 기준으로는 그린다
    return asset_history(
        initial_cash=repo.initial_cash(),
        trades=trades,
        start=start,
        end=today,
        closes=closes,
        usd_krw=usd_krw,
    )
