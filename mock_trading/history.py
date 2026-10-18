"""자산 추이: 날짜별 총자산을 거래 장부와 과거 종가로 다시 계산한다.

앱을 켠 날만 기록하는 방식이 아니라, 장부(언제 무엇을 몇 주 샀는지)와 그날의 종가·환율로
계좌를 연 날부터 오늘까지 매일의 평가액을 만든다. 그래서 앱을 안 켠 날도 빈칸이 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from .domain import Currency, Side, Trade, krw

# 한 번에 그릴 최대 기간. 장부가 아주 길어도 조회·계산량이 끝없이 커지지 않게 한다.
MAX_HISTORY_DAYS = 366 * 3
# 첫날에도 "직전 거래일 종가"가 있도록 기간 시작보다 이만큼 앞에서부터 받는다(연휴 대비).
DOWNLOAD_BUFFER_DAYS = 10
# 과거 종가는 바뀌지 않으므로 한 번 받으면 기억해 두고, 최근 며칠만 이 간격으로 다시 받는다.
RECENT_DAYS = 7
REFRESH_INTERVAL = timedelta(minutes=10)


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
    known = sorted(closes)  # 기간 시작 전 값도 포함 → 첫날이 휴장이어도 직전 종가를 쓴다
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

    # 장부와 같은 순서(id)로 반영한다. PC 시계를 되돌려 날짜가 거꾸로 기록돼도 매도가 매수보다 먼저
    # 반영되지 않도록, 반영 날짜는 앞 거래보다 이르지 않게 보정한다.
    trades = sorted(trades, key=lambda t: t.id)
    effective_days = []
    latest = date.min
    for item in trades:
        latest = max(latest, item.executed_at.date())
        effective_days.append(latest)
    cash = initial_cash
    quantity: dict[str, int] = {}
    cost: dict[str, Decimal] = {}
    currency: dict[str, Currency] = {}
    last_price: dict[str, tuple[Decimal, Decimal]] = {}  # 장부에 남은 마지막 체결가와 환율
    points = []
    t = 0
    for day in days:
        # 그날 끝까지의 거래를 반영 (장부 계산과 같은 이동평균 규칙)
        while t < len(trades) and effective_days[t] <= day:
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


_FX_KEY = "__USD_KRW__"


@dataclass
class DailyCloseCache:
    """종목·환율별 일봉 종가를 기억한다. 과거 값은 다시 받지 않고, 최근 며칠만 가끔 새로 받는다.

    한 키는 성공하든 실패하든 REFRESH_INTERVAL에 한 번만 시도한다. 요청 제한(429)에 걸렸을 때
    계속 두드려 제한을 늘리거나 주문용 시세까지 막지 않기 위해서다.
    """
    closes: dict[str, dict[date, Decimal]] = field(default_factory=dict)
    covered_from: dict[str, date] = field(default_factory=dict)  # 실제로 값을 받은 구간의 시작
    attempted_at: dict[str, datetime] = field(default_factory=dict)

    def get(self, provider, symbols: list[str], start: date, *, include_fx: bool, today: date, now: datetime):
        keys = list(symbols) + ([_FX_KEY] if include_fx else [])
        due = [k for k in keys if k not in self.attempted_at or now - self.attempted_at[k] >= REFRESH_INTERVAL]
        full = [k for k in due if k not in self.covered_from or self.covered_from[k] > start]
        recent = [k for k in due if k not in full]
        for group, group_start in ((full, start), (recent, today - timedelta(days=RECENT_DAYS))):
            if not group:
                continue
            for key in group:
                self.attempted_at[key] = now
            group_symbols = [k for k in group if k != _FX_KEY]
            try:
                closes, usd_krw = provider.get_daily_closes(
                    group_symbols, group_start, include_usd_krw=_FX_KEY in group
                )
            except Exception:
                continue  # 이번엔 못 받았으면 기억해 둔 값으로 그리고, 간격이 지난 뒤 다시 시도
            received = dict(closes)
            if _FX_KEY in group:
                received[_FX_KEY] = usd_krw
            for key in group:
                data = received.get(key)
                if not data:
                    continue  # 응답에서 빠진 종목은 "받음"으로 기록하지 않는다 → 다음에 전체 구간을 다시 시도
                self.closes.setdefault(key, {}).update(data)
                self.covered_from[key] = min(self.covered_from.get(key, group_start), group_start)
        return (
            {s: self.closes.get(s, {}) for s in symbols},
            self.closes.get(_FX_KEY, {}) if include_fx else {},
        )


def load_asset_history(
    repo, provider, today: date, cache: DailyCloseCache | None = None, now: datetime | None = None
) -> list[AssetPoint]:
    """계좌를 연 날부터 오늘까지(최대 MAX_HISTORY_DAYS). 필요한 종목·환율의 과거 종가는 일괄로 받고 기억해 둔다."""
    opened = min(repo.account_opened_on(), today)  # 미래 날짜는 오늘로
    start = max(opened, today - timedelta(days=MAX_HISTORY_DAYS))
    download_from = start - timedelta(days=DOWNLOAD_BUFFER_DAYS)
    trades = repo.trades()
    symbols = sorted({t.symbol for t in trades})
    has_usd = any(t.currency is Currency.USD for t in trades)
    closes: DailyCloses = {}
    usd_krw: dict[date, Decimal] = {}
    if symbols and getattr(provider, "get_daily_closes", None) is not None:
        cache = cache if cache is not None else DailyCloseCache()
        closes, usd_krw = cache.get(
            provider, symbols, download_from, include_fx=has_usd, today=today, now=now or datetime.now()
        )
    return asset_history(
        initial_cash=repo.initial_cash(),
        trades=trades,
        start=start,
        end=today,
        closes=closes,
        usd_krw=usd_krw,
    )


# 시장과 비교: 지수 자체(^KS11 등)는 종목 코드 규칙 밖이라, 지수를 따라가는 ETF의 종가로 수익률을 잰다.
# (코드, 이름, 통화). 달러 ETF는 원화로 환산해 원화 계좌와 같은 기준으로 비교한다. 배당은 빠진 가격 수익률이다.
BENCHMARKS = (
    ("069500.KS", "코스피200", Currency.KRW),
    ("SPY", "S&P500", Currency.USD),
)


@dataclass(frozen=True)
class BenchmarkSeries:
    symbol: str
    name: str
    returns: list[tuple[date, Decimal]]  # (날짜, 첫날 대비 수익률 %)


def benchmark_returns(
    days: list[date], closes: DailyCloses, usd_krw: dict[date, Decimal]
) -> list[BenchmarkSeries]:
    """days 첫날을 0%로 한 지수 ETF 수익률. 첫날 값(직전 거래일 포함)을 모르면 그 지수는 빼고 돌려준다."""
    if not days:
        return []
    fx = _forward_filled(usd_krw, days)
    out = []
    for symbol, name, currency in BENCHMARKS:
        prices = _forward_filled(closes.get(symbol, {}), days)
        values: dict[date, Decimal] = {}
        for day, price in prices.items():
            rate = Decimal(1) if currency is Currency.KRW else fx.get(day)
            if rate is not None:
                values[day] = price * rate
        base = values.get(days[0])
        if base is None:
            continue  # 기준일 값이 없으면 비교가 틀어지므로 그리지 않는다
        out.append(BenchmarkSeries(symbol, name, [(d, (values[d] / base - 1) * 100) for d in days if d in values]))
    return out


def load_benchmarks(
    provider, start: date, today: date, cache: DailyCloseCache, now: datetime | None = None
) -> list[BenchmarkSeries]:
    """자산 추이와 같은 기간(start~today)의 지수 ETF 수익률. 종가는 자산 추이와 같은 캐시로 받는다."""
    if getattr(provider, "get_daily_closes", None) is None or today < start:
        return []
    start = max(start, today - timedelta(days=MAX_HISTORY_DAYS))
    days = [start + timedelta(days=n) for n in range((today - start).days + 1)]
    symbols = [symbol for symbol, _, _ in BENCHMARKS]
    closes, usd_krw = cache.get(
        provider, symbols, start - timedelta(days=DOWNLOAD_BUFFER_DAYS),
        include_fx=any(c is Currency.USD for _, _, c in BENCHMARKS), today=today, now=now or datetime.now(),
    )
    return benchmark_returns(days, closes, usd_krw)
