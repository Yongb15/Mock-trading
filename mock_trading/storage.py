"""SQLite 저장소.

거래 내역(trades)은 추가만 하는 장부다. 현금과 보유 수량은 따로 저장하지 않고
장부에서 계산하므로, 둘이 서로 어긋나는 일이 생기지 않는다.
모든 쿼리는 파라미터 바인딩(?)을 쓴다.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from functools import wraps
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from .domain import Currency, Holding, LimitOrder, OrderStatus, Side, Trade, TradingError, krw, to_decimal

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    initial_cash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    generation    TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at  TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity     INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity > 0),
    price        TEXT    NOT NULL,
    currency     TEXT    NOT NULL CHECK (currency IN ('KRW', 'USD')),
    fx_rate      TEXT    NOT NULL,
    amount_krw   TEXT    NOT NULL,
    fee_krw      TEXT    NOT NULL
);
-- 지정가 주문. 계좌에 딸린 기록이라 계좌를 초기화하면 함께 지운다.
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity     INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity > 0),
    limit_price  TEXT    NOT NULL,
    currency     TEXT    NOT NULL CHECK (currency IN ('KRW', 'USD')),
    status       TEXT    NOT NULL CHECK (status IN ('OPEN', 'FILLED', 'CANCELLED', 'REJECTED')),
    trade_id     INTEGER REFERENCES trades(id),
    note         TEXT    NOT NULL DEFAULT '',
    closed_at    TEXT,
    fx_at_order  TEXT,
    armed        INTEGER NOT NULL DEFAULT 0 CHECK (armed IN (0, 1))
);
-- 관심 종목은 계좌가 아니라 사용자 설정이라 계좌를 초기화해도 남는다.
CREATE TABLE IF NOT EXISTS watchlist (
    symbol    TEXT NOT NULL PRIMARY KEY,
    name      TEXT NOT NULL,
    currency  TEXT NOT NULL CHECK (currency IN ('KRW', 'USD')),
    added_at  TEXT NOT NULL
);
"""


class LedgerError(Exception):
    """DB에 저장된 장부가 손상되어 계산할 수 없음. 계좌 초기화로 복구한다."""


@dataclass(frozen=True)
class Ledger:
    cash: Decimal
    holdings: dict[str, Holding]
    realized_profit: Decimal


@dataclass(frozen=True)
class WatchItem:
    symbol: str
    name: str
    currency: Currency


def _stored_decimal(value: str, what: str) -> Decimal:
    try:
        return to_decimal(value)
    except TradingError:
        raise LedgerError(f"장부의 {what} 값이 손상되었습니다: {value!r}") from None


def default_db_path() -> Path:
    return Path.home() / ".mock_trading" / "mock_trading.db"


def _locked(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Repository:
    """SQLite 연결 하나를 감싼다. 모든 공개 메서드와 트랜잭션은 같은 RLock을 잡으므로
    화면 스레드와 작업 스레드가 동시에 불러도 한 번에 하나씩만 실행된다."""

    def __init__(self, path: Path | str = ":memory:", *, check_same_thread: bool = True) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: 트랜잭션을 transaction()에서 직접 연다.
        # check_same_thread=False는 호출하는 쪽이 한 번에 한 스레드만 쓰도록 보장할 때만 쓴다(UI의 단일 작업 스레드).
        self._conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._lock = threading.RLock()

    def _migrate(self) -> None:
        """v0.1.0에서 만든 DB 파일에는 generation 칸이 없다. 없으면 추가한다(기존 계좌는 created_at으로 대신)."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(account)")}
        if "generation" not in columns:
            self._conn.execute("ALTER TABLE account ADD COLUMN generation TEXT")
        order_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(orders)")}
        if "fx_at_order" not in order_columns:
            self._conn.execute("ALTER TABLE orders ADD COLUMN fx_at_order TEXT")
        if "armed" not in order_columns:
            self._conn.execute("ALTER TABLE orders ADD COLUMN armed INTEGER NOT NULL DEFAULT 0")

    @_locked
    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """잔고 확인과 거래 기록을 한 번에 처리한다. 중간에 실패하면 전부 되돌린다.
        트랜잭션이 끝날 때까지 잠금을 잡고 있어 다른 스레드의 쿼리가 끼어들지 못한다."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # ---- 계좌 ----
    @_locked
    def has_account(self) -> bool:
        return self._conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone() is not None

    @_locked
    def initial_cash(self) -> Decimal:
        row = self._conn.execute("SELECT initial_cash FROM account WHERE id = 1").fetchone()
        if row is None:
            raise LookupError("계좌가 아직 없습니다.")
        return _stored_decimal(row["initial_cash"], "기초 자금")

    def hold_for_exit(self, timeout: float) -> bool:
        """종료 직전에만 쓴다: 진행 중인 트랜잭션이 끝날 때까지 기다린 뒤 잠금을 잡고 놓지 않는다."""
        return self._lock.acquire(timeout=timeout)

    @_locked
    def account_opened_on(self) -> date:
        row = self._conn.execute("SELECT created_at FROM account WHERE id = 1").fetchone()
        if row is None:
            raise LookupError("계좌가 아직 없습니다.")
        try:
            return datetime.fromisoformat(row["created_at"]).date()
        except ValueError:
            raise LedgerError(f"계좌 개설일이 손상되었습니다: {row['created_at']!r}") from None

    @_locked
    def account_generation(self) -> str | None:
        """계좌를 초기화할 때마다 바뀌는 값. 주문이 시작된 계좌와 기록할 계좌가 같은지 확인하는 데 쓴다."""
        row = self._conn.execute("SELECT generation, created_at FROM account WHERE id = 1").fetchone()
        if row is None:
            return None
        return row["generation"] or row["created_at"]

    @_locked
    def reset_account(self, initial_cash: Decimal) -> None:
        """기초 자금을 새로 받고 거래 내역을 모두 지운다."""
        with self.transaction():
            self._conn.execute("DELETE FROM orders")
            self._conn.execute("DELETE FROM trades")
            self._conn.execute("DELETE FROM account")
            self._conn.execute(
                "INSERT INTO account (id, initial_cash, created_at, generation) VALUES (1, ?, ?, ?)",
                # 세대는 시각이 아니라 무작위 값이라 아무리 빨리 초기화해도 겹치지 않는다.
                (str(initial_cash), datetime.now().isoformat(timespec="seconds"), uuid.uuid4().hex),
            )

    # ---- 거래 장부 ----
    @_locked
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
        # 마지막 방어선: 장부에는 유한한 값만, 금액은 양수만 들어간다.
        if not all(isinstance(v, Decimal) and v.is_finite() for v in (price, fx_rate, amount_krw, fee_krw)):
            raise TradingError("금액에 올바르지 않은 숫자가 있어 기록하지 않았습니다.")
        if price <= 0 or fx_rate <= 0 or amount_krw <= 0 or fee_krw < 0:
            raise TradingError("금액이 0 이하라 기록하지 않았습니다.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise TradingError("수량이 올바르지 않아 기록하지 않았습니다.")
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

    @_locked
    def trades(self) -> list[Trade]:
        rows = self._conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        return [self._row_to_trade(row) for row in rows]

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> Trade:
        quantity = row["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise LedgerError(f"거래 #{row['id']}의 수량 값이 손상되었습니다: {quantity!r}")
        try:
            return Trade(
                id=row["id"],
                executed_at=datetime.fromisoformat(row["executed_at"]),
                symbol=row["symbol"],
                name=row["name"],
                side=Side(row["side"]),
                quantity=row["quantity"],
                price=_stored_decimal(row["price"], "체결가"),
                currency=Currency(row["currency"]),
                fx_rate=_stored_decimal(row["fx_rate"], "환율"),
                amount_krw=_stored_decimal(row["amount_krw"], "체결 금액"),
                fee_krw=_stored_decimal(row["fee_krw"], "수수료"),
            )
        except (ValueError, TypeError) as error:
            raise LedgerError(f"거래 #{row['id']}의 기록이 손상되었습니다: {error}") from None

    # ---- 지정가 주문 ----
    @_locked
    def add_order(
        self, *, symbol: str, name: str, side: Side, quantity: int, limit_price: Decimal, currency: Currency,
        fx_at_order: Decimal | None = None,
    ) -> LimitOrder:
        if not (isinstance(limit_price, Decimal) and limit_price.is_finite() and limit_price > 0):
            raise TradingError("지정가가 올바르지 않아 주문을 저장하지 않았습니다.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise TradingError("수량이 올바르지 않아 주문을 저장하지 않았습니다.")
        created_at = datetime.now().replace(microsecond=0)
        cursor = self._conn.execute(
            """
            INSERT INTO orders (created_at, symbol, name, side, quantity, limit_price, currency, status, fx_at_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (created_at.isoformat(), symbol, name, side.value, quantity, str(limit_price), currency.value,
             str(fx_at_order) if fx_at_order is not None else None),
        )
        return LimitOrder(cursor.lastrowid, created_at, symbol, name, side, quantity, limit_price, currency,
                          OrderStatus.OPEN, fx_at_order=fx_at_order)

    @_locked
    def set_order_armed(self, order_id: int, armed: bool) -> None:
        self._conn.execute(
            "UPDATE orders SET armed = ? WHERE id = ? AND status = 'OPEN'", (1 if armed else 0, order_id)
        )

    @_locked
    def orders(self, *, open_only: bool = False, limit: int = 200) -> list[LimitOrder]:
        """최근 주문부터(open_only면 대기 중인 것만, 오래된 것부터 = 체결 확인 순서)."""
        if open_only:
            rows = self._conn.execute("SELECT * FROM orders WHERE status = 'OPEN' ORDER BY id").fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        orders = []
        for row in rows:
            try:
                orders.append(LimitOrder(
                    id=row["id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    symbol=row["symbol"],
                    name=row["name"],
                    side=Side(row["side"]),
                    quantity=row["quantity"],
                    limit_price=_stored_decimal(row["limit_price"], "지정가"),
                    currency=Currency(row["currency"]),
                    status=OrderStatus(row["status"]),
                    trade_id=row["trade_id"],
                    note=row["note"],
                    fx_at_order=_stored_decimal(row["fx_at_order"], "주문 환율") if row["fx_at_order"] else None,
                    armed=bool(row["armed"]),
                ))
            except (ValueError, TypeError, LedgerError):
                continue  # 손상된 주문은 체결하지 않는다(돈이 움직이기 전이라 건너뛰어도 안전)
        return orders

    @_locked
    def close_order(self, order_id: int, status: OrderStatus, *, trade_id: int | None = None, note: str = "") -> bool:
        """대기 중인 주문만 닫는다. 이미 닫혔으면 False — 같은 주문이 두 번 체결되지 않게 하는 마지막 장치."""
        if status is OrderStatus.OPEN:
            raise ValueError("OPEN으로는 닫을 수 없습니다.")
        cursor = self._conn.execute(
            "UPDATE orders SET status = ?, trade_id = ?, note = ?, closed_at = ? WHERE id = ? AND status = 'OPEN'",
            (status.value, trade_id, note[:200], datetime.now().isoformat(timespec="seconds"), order_id),
        )
        return cursor.rowcount == 1

    # ---- 관심 종목 ----
    def _purge_invalid_watch(self) -> None:
        """손상된 관심 종목 행(코드 형식·통화가 이상한 행)은 보여줄 수도 지울 수도 없으므로 정리한다."""
        from .market_data import is_valid_symbol  # 순환 import 방지

        # symbol이 NULL이면 "symbol = ?"로는 지워지지 않으므로 rowid로 지운다.
        for row in self._conn.execute("SELECT rowid, symbol, currency FROM watchlist").fetchall():
            if not is_valid_symbol(row["symbol"]) or row["currency"] not in ("KRW", "USD"):
                self._conn.execute("DELETE FROM watchlist WHERE rowid = ?", (row["rowid"],))

    @_locked
    def add_watch(self, symbol: str, name: str, currency: Currency, *, limit: int) -> bool:
        """추가하면 True, 이미 있으면 False. 개수 확인과 추가를 한 트랜잭션에서 한다."""
        with self.transaction():
            self._purge_invalid_watch()  # 손상된 행이 한도를 차지하지 않게
            if self._conn.execute("SELECT 1 FROM watchlist WHERE symbol = ?", (symbol,)).fetchone():
                return False
            count = self._conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
            if count >= limit:
                raise TradingError(f"관심 종목은 {limit}개까지 담을 수 있습니다.")
            self._conn.execute(
                "INSERT INTO watchlist (symbol, name, currency, added_at) VALUES (?, ?, ?, ?)",
                (symbol, name, currency.value, datetime.now().isoformat(timespec="seconds")),
            )
            return True

    @_locked
    def remove_watch(self, *symbols: str) -> bool:
        """저장된 값과 같은 코드를 지운다(바인딩이라 어떤 문자열이든 안전). 하나라도 지웠으면 True."""
        removed = 0
        for symbol in symbols:
            removed += self._conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,)).rowcount
        return removed > 0

    @_locked
    def watchlist(self) -> list[WatchItem]:
        with self.transaction():
            self._purge_invalid_watch()
        rows = self._conn.execute("SELECT symbol, name, currency FROM watchlist ORDER BY added_at, symbol").fetchall()
        items = []
        for row in rows:
            items.append(WatchItem(row["symbol"], row["name"], Currency(row["currency"])))
        return items

    @_locked
    def is_watched(self, *symbols: str) -> bool:
        return any(
            self._conn.execute("SELECT 1 FROM watchlist WHERE symbol = ?", (s,)).fetchone() for s in symbols
        )

    # ---- 장부에서 계산하는 값 ----
    @_locked
    def ledger(self) -> Ledger:
        """장부를 처음부터 한 번 훑어 현금·보유 종목·실현 손익을 함께 계산한다.

        이동평균법: 매도하면 남은 원가를 매도 수량 비율만큼 줄이고, 줄어든 원가와 매도 대금의 차이가 실현 손익이다.
        """
        cash = self.initial_cash()
        realized = Decimal(0)
        holdings: dict[str, Holding] = {}
        for trade in self.trades():
            current = holdings.get(trade.symbol)
            held_quantity = current.quantity if current else 0
            held_cost = current.cost_krw if current else Decimal(0)
            if trade.side is Side.BUY:
                cash -= trade.amount_krw + trade.fee_krw
                quantity = held_quantity + trade.quantity
                cost = held_cost + trade.amount_krw + trade.fee_krw
            else:
                if trade.quantity > held_quantity:
                    raise LedgerError(f"거래 #{trade.id}: {trade.symbol}을 보유 수량보다 많이 매도한 기록이 있습니다.")
                cash += trade.amount_krw - trade.fee_krw
                quantity = held_quantity - trade.quantity
                cost = krw(held_cost * quantity / held_quantity)
                realized += trade.amount_krw - trade.fee_krw - (held_cost - cost)
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
        return Ledger(cash=cash, holdings=holdings, realized_profit=realized)

    def cash(self) -> Decimal:
        return self.ledger().cash

    def holdings(self) -> dict[str, Holding]:
        return self.ledger().holdings

    def realized_profit(self) -> Decimal:
        return self.ledger().realized_profit
