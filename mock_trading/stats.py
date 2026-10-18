"""매매 통계: 거래 장부만으로 연습 결과를 돌아본다(승률, 평균 수익·손실, 보유 기간, 종목별 실현 손익).

실현 손익은 장부(storage.Repository.ledger)와 같은 이동평균 규칙으로 계산한다. 그래서 매도 한 건 한 건의
손익을 더하면 계좌 요약의 '실현 손익'과 정확히 같다(테스트로 확인).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .domain import Side, Trade, krw


@dataclass(frozen=True)
class ClosedTrade:
    """매도 한 건의 결과."""
    trade_id: int
    symbol: str
    name: str
    profit_krw: Decimal      # 매도 대금 - 수수료·세금 - 줄어든 원가(이동평균)
    cost_krw: Decimal        # 이번 매도로 줄어든 원가
    holding_days: int        # 평균 취득일(수량 가중, 이동평균 원가와 같은 규칙)부터 매도한 날까지

    @property
    def return_rate(self) -> Decimal:
        return self.profit_krw / self.cost_krw * 100 if self.cost_krw else Decimal(0)


@dataclass(frozen=True)
class SymbolResult:
    symbol: str
    name: str
    sells: int
    profit_krw: Decimal


@dataclass(frozen=True)
class TradeStats:
    closed: list[ClosedTrade]
    by_symbol: list[SymbolResult]  # 실현 손익이 큰 순서

    @property
    def sells(self) -> int:
        return len(self.closed)

    @property
    def wins(self) -> list[ClosedTrade]:
        return [c for c in self.closed if c.profit_krw > 0]

    @property
    def losses(self) -> list[ClosedTrade]:
        return [c for c in self.closed if c.profit_krw < 0]

    @property
    def win_rate(self) -> Decimal | None:
        """이익 본 매도 / 전체 매도(%). 매도가 없으면 None. 손익이 정확히 0인 매도는 분모에만 든다."""
        return Decimal(len(self.wins)) / self.sells * 100 if self.sells else None

    @property
    def total_profit(self) -> Decimal:
        return sum((c.profit_krw for c in self.closed), Decimal(0))

    @property
    def average_win(self) -> Decimal | None:
        return _average([c.profit_krw for c in self.wins])

    @property
    def average_loss(self) -> Decimal | None:
        return _average([c.profit_krw for c in self.losses])

    @property
    def average_holding_days(self) -> Decimal | None:
        return _average([Decimal(c.holding_days) for c in self.closed], unit=Decimal("0.1"))

    @property
    def best(self) -> ClosedTrade | None:
        return max(self.closed, key=lambda c: c.profit_krw) if self.closed else None

    @property
    def worst(self) -> ClosedTrade | None:
        return min(self.closed, key=lambda c: c.profit_krw) if self.closed else None


def _average(values: list[Decimal], unit: Decimal = Decimal(1)) -> Decimal | None:
    return (sum(values, Decimal(0)) / len(values)).quantize(unit) if values else None


def trade_stats(trades: list[Trade]) -> TradeStats:
    """장부 순서(id)대로 훑어 매도마다 실현 손익과 보유 기간을 낸다."""
    quantity: dict[str, int] = {}
    cost: dict[str, Decimal] = {}
    # 평균 취득일(서수 날짜, 수량 가중). 이동평균 원가처럼 매수마다 섞고, 일부 매도에는 그대로, 0주가 되면 버린다.
    # '처음 산 날'로 세면 물타기·분할 매수에서 보유 기간이 크게 부풀려진다(Mock_QA TS1).
    avg_day: dict[str, Decimal] = {}
    closed: list[ClosedTrade] = []
    names: dict[str, str] = {}
    for trade in sorted(trades, key=lambda t: t.id):
        held = quantity.get(trade.symbol, 0)
        held_cost = cost.get(trade.symbol, Decimal(0))
        names[trade.symbol] = trade.name
        day = Decimal(trade.executed_at.date().toordinal())
        if trade.side is Side.BUY:
            previous = avg_day.get(trade.symbol, day) if held else day
            avg_day[trade.symbol] = (previous * held + day * trade.quantity) / (held + trade.quantity)
            quantity[trade.symbol] = held + trade.quantity
            cost[trade.symbol] = held_cost + trade.amount_krw + trade.fee_krw
            continue
        if not held or trade.quantity > held:
            continue  # 장부가 깨진 경우는 ledger()가 오류로 알린다. 통계는 건너뛴다
        remaining = held - trade.quantity
        new_cost = krw(held_cost * remaining / held)
        reduced = held_cost - new_cost
        held_days = (day - avg_day.get(trade.symbol, day)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        closed.append(ClosedTrade(
            trade.id, trade.symbol, trade.name,
            trade.amount_krw - trade.fee_krw - reduced, reduced,
            max(0, int(held_days)),
        ))
        quantity[trade.symbol], cost[trade.symbol] = remaining, new_cost

    totals: dict[str, list] = {}
    for item in closed:
        entry = totals.setdefault(item.symbol, [0, Decimal(0)])
        entry[0] += 1
        entry[1] += item.profit_krw
    by_symbol = sorted(
        (SymbolResult(symbol, names[symbol], n, profit) for symbol, (n, profit) in totals.items()),
        key=lambda r: r.profit_krw, reverse=True,
    )
    return TradeStats(closed, by_symbol)
