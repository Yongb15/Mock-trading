"""SQLite 저장소.

거래 내역(trades)은 추가만 하는 장부다. 현금과 보유 수량은 따로 저장하지 않고
장부에서 계산하므로, 둘이 서로 어긋나는 일이 생기지 않는다.
모든 쿼리는 파라미터 바인딩(?)을 쓴다.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from .domain import Currency, Holding, Side, Trade, krw

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    initial_cash  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at  TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    price        TEXT    NOT NULL,
    currency     TEXT    NOT NULL CHECK (currency IN ('KRW', 'USD')),
    fx_rate      TEXT    NOT NULL,
    amount_krw   TEXT    NOT NULL,
    fee_krw      TEXT    NOT NULL
);
"""


def default_db_path() -> Path:
    return Path.home() / ".mock_trading" / "mock_trading.db"


class Repository:
    def __init__(self, path: Path | str = ":memory:", *, check_same_thread: bool = True) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: 트랜잭션을 transaction()에서 직접 연다.
        # check_same_thread=False는 호출하는 쪽이 한 번에 한 스레드만 쓰도록 보장할 때만 쓴다(UI의 단일 작업 스레드).
        self._conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """잔고 확인과 거래 기록을 한 번에 처리한다. 중간에 실패하면 전부 되돌린다."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ---- 계좌 ----
    def has_account(self) -> bool:
        return self._conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone() is not None

    def initial_cash(self) -> Decimal:
        row = self._conn.execute("SELECT initial_cash FROM account WHERE id = 1").fetchone()
        if row is None:
            raise LookupError("계좌가 아직 없습니다.")
        return Decimal(row["initial_cash"])

    def reset_account(self, initial_cash: Decimal) -> None:
        """기초 자금을 새로 받고 거래 내역을 모두 지운다."""
        with self.transaction():
            self._conn.execute("DELETE FROM trades")
            self._conn.execute("DELETE FROM account")
            self._conn.execute(
                "INSERT INTO account (id, initial_cash, created_at) VALUES (1, ?, ?)",
                (str(initial_cash), datetime.now().isoformat(timespec="seconds")),
            )

    # ---- 거래 장부 ----
    def add_trade(
        self,
        *,
        symbol: str,
        name: str,
        side: Side,
        quantity: int,
        price: Decimal,
        currency: Currency,
        fx_rate: Decimal,
        amount_krw: Decimal,
        fee_krw: Decimal,
        executed_at: datetime | None = None,
    ) -> Trade:
        executed_at = executed_at or datetime.now()
        cursor = self._conn.execute(
            """
            INSERT INTO trades
                (executed_at, symbol, name, side, quantity, price, currency, fx_rate, amount_krw, fee_krw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                executed_at.isoformat(timespec="seconds"),
                symbol,
                name,
                side.value,
                quantity,
                str(price),
                currency.value,
                str(fx_rate),
                str(amount_krw),
                str(fee_krw),
            ),
        )
        return Trade(
            id=cursor.lastrowid,
            executed_at=executed_at.replace(microsecond=0),
            symbol=symbol,
            name=name,
            side=side,
            quantity=quantity,
            price=price,
            currency=currency,
            fx_rate=fx_rate,
            amount_krw=amount_krw,
            fee_krw=fee_krw,
        )

    def trades(self) -> list[Trade]:
        rows = self._conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        return [
            Trade(
                id=row["id"],
                executed_at=datetime.fromisoformat(row["executed_at"]),
                symbol=row["symbol"],
                name=row["name"],
                side=Side(row["side"]),
                quantity=row["quantity"],
                price=Decimal(row["price"]),
                currency=Currency(row["currency"]),
                fx_rate=Decimal(row["fx_rate"]),
                amount_krw=Decimal(row["amount_krw"]),
                fee_krw=Decimal(row["fee_krw"]),
            )
            for row in rows
        ]

    # ---- 장부에서 계산하는 값 ----
    def cash(self) -> Decimal:
        cash = self.initial_cash()
        for trade in self.trades():
            if trade.side is Side.BUY:
                cash -= trade.amount_krw + trade.fee_krw
            else:
                cash += trade.amount_krw - trade.fee_krw
        return cash

    def holdings(self) -> dict[str, Holding]:
        """이동평균법: 매도하면 남은 원가를 매도 수량 비율만큼 줄인다."""
        holdings: dict[str, Holding] = {}
        for trade in self.trades():
            current = holdings.get(trade.symbol)
            if trade.side is Side.BUY:
                quantity = (current.quantity if current else 0) + trade.quantity
                cost = (current.cost_krw if current else Decimal(0)) + trade.amount_krw + trade.fee_krw
            else:
                if current is None or current.quantity < trade.quantity:
                    raise ValueError(f"장부가 손상되었습니다: {trade.symbol} 보유 수량보다 많이 매도됨")
                quantity = current.quantity - trade.quantity
                cost = krw(current.cost_krw * quantity / current.quantity)
            if quantity == 0:
                holdings.pop(trade.symbol, None)
            else:
                holdings[trade.symbol] = Holding(
                    symbol=trade.symbol,
                    name=trade.name,
                    currency=trade.currency,
                    quantity=quantity,
                    cost_krw=cost,
                )
        return holdings

    def realized_profit(self) -> Decimal:
        """매도로 확정된 손익 합계 (수수료 반영)."""
        held: dict[str, tuple[int, Decimal]] = {}
        realized = Decimal(0)
        for trade in self.trades():
            quantity, cost = held.get(trade.symbol, (0, Decimal(0)))
            if trade.side is Side.BUY:
                held[trade.symbol] = (quantity + trade.quantity, cost + trade.amount_krw + trade.fee_krw)
            else:
                remaining = quantity - trade.quantity
                remaining_cost = krw(cost * remaining / quantity)
                sold_cost = cost - remaining_cost
                realized += trade.amount_krw - trade.fee_krw - sold_cost
                held[trade.symbol] = (remaining, remaining_cost)
        return realized
