"""주문 처리. 수량·가격·잔고 검증은 전부 여기서 한다(화면은 입력만 넘긴다)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .domain import (
    MAX_ORDER_QUANTITY,
    Currency,
    Position,
    Quote,
    Side,
    Trade,
    TradingError,
    krw,
    krw_floor,
    price_in,
)
from .market_data import QuoteProvider, normalize_symbol
from .storage import Repository

MIN_INITIAL_CASH = Decimal("100000")
MAX_INITIAL_CASH = Decimal("100000000000")  # 1000억


@dataclass(frozen=True)
class FeePolicy:
    """실제 증권사 수수료·세금을 단순화한 값. 연습용이므로 필요하면 바꿔서 쓴다."""
    commission_rate: Decimal = Decimal("0.00015")   # 매수·매도 수수료 0.015%
    kr_sell_tax_rate: Decimal = Decimal("0.0020")   # 국내 주식 매도 시 거래세 0.20%로 가정

    def fee(self, side: Side, currency: Currency, amount_krw: Decimal) -> Decimal:
        rate = self.commission_rate
        if side is Side.SELL and currency is Currency.KRW:
            rate += self.kr_sell_tax_rate
        return krw_floor(amount_krw * rate)


@dataclass(frozen=True)
class AccountSummary:
    initial_cash: Decimal
    cash: Decimal
    stock_value: Decimal
    realized_profit: Decimal

    @property
    def total(self) -> Decimal:
        return self.cash + self.stock_value

    @property
    def total_return_rate(self) -> Decimal:
        return (self.total - self.initial_cash) / self.initial_cash * 100


class Broker:
    def __init__(self, repo: Repository, provider: QuoteProvider, fees: FeePolicy | None = None) -> None:
        self.repo = repo
        self.provider = provider
        self.fees = fees or FeePolicy()

    # ---- 계좌 ----
    def open_account(self, initial_cash: Decimal | int | str) -> None:
        try:
            amount = Decimal(str(initial_cash))
        except Exception:
            raise TradingError("기초 자금은 숫자여야 합니다.") from None
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise TradingError("기초 자금은 원 단위 정수여야 합니다.")
        if not MIN_INITIAL_CASH <= amount <= MAX_INITIAL_CASH:
            raise TradingError(f"기초 자금은 {MIN_INITIAL_CASH:,}원 ~ {MAX_INITIAL_CASH:,}원 사이여야 합니다.")
        self.repo.reset_account(amount)

    def fx_rate_for(self, currency: Currency) -> Decimal:
        return Decimal(1) if currency is Currency.KRW else self.provider.get_usd_krw()

    # ---- 주문 ----
    def buy(self, symbol: str, quantity: int) -> Trade:
        return self._execute(Side.BUY, symbol, quantity)

    def sell(self, symbol: str, quantity: int) -> Trade:
        return self._execute(Side.SELL, symbol, quantity)

    def _execute(self, side: Side, symbol: str, quantity: int) -> Trade:
        self._validate_quantity(quantity)
        quote = self.provider.get_quote(normalize_symbol(symbol))
        if quote.price <= 0:
            raise TradingError("현재가가 올바르지 않아 주문할 수 없습니다.")
        fx_rate = self.fx_rate_for(quote.currency)
        price = price_in(quote.currency, quote.price)
        amount = krw(price * fx_rate * quantity)
        fee = self.fees.fee(side, quote.currency, amount)

        # 확인과 기록을 한 트랜잭션에서: 그 사이에 다른 주문이 끼어들 수 없다.
        with self.repo.transaction():
            if side is Side.BUY:
                cash = self.repo.cash()
                if amount + fee > cash:
                    raise TradingError(f"현금이 부족합니다. 필요 {amount + fee:,}원 / 보유 {cash:,}원")
            else:
                held = self.repo.holdings().get(quote.symbol)
                held_quantity = held.quantity if held else 0
                if quantity > held_quantity:
                    raise TradingError(f"보유 수량이 부족합니다. 주문 {quantity}주 / 보유 {held_quantity}주")
            return self.repo.add_trade(
                symbol=quote.symbol,
                name=quote.name,
                side=side,
                quantity=quantity,
                price=price,
                currency=quote.currency,
                fx_rate=fx_rate,
                amount_krw=amount,
                fee_krw=fee,
            )

    @staticmethod
    def _validate_quantity(quantity: int) -> None:
        # bool은 int의 하위 타입이라 True가 1주로 통과하지 않게 따로 막는다.
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise TradingError("수량은 정수여야 합니다.")
        if quantity <= 0:
            raise TradingError("수량은 1주 이상이어야 합니다.")
        if quantity > MAX_ORDER_QUANTITY:
            raise TradingError(f"한 번에 {MAX_ORDER_QUANTITY:,}주까지 주문할 수 있습니다.")

    def max_buy_quantity(self, quote: Quote) -> int:
        """현재 현금으로 살 수 있는 최대 수량 (수수료 포함)."""
        fx_rate = self.fx_rate_for(quote.currency)
        unit = price_in(quote.currency, quote.price) * fx_rate
        if unit <= 0:
            return 0
        cash = self.repo.cash()
        quantity = int(cash / (unit * (1 + self.fees.commission_rate)))
        quantity = min(quantity, MAX_ORDER_QUANTITY)
        while quantity > 0:
            amount = krw(unit * quantity)
            if amount + self.fees.fee(Side.BUY, quote.currency, amount) <= cash:
                break
            quantity -= 1
        return quantity

    # ---- 평가 ----
    def positions(self) -> list[Position]:
        holdings = self.repo.holdings()
        if not holdings:
            return []
        usd_krw = self.provider.get_usd_krw() if any(h.currency is Currency.USD for h in holdings.values()) else None
        positions = []
        for holding in holdings.values():
            quote = self.provider.get_quote(holding.symbol)
            fx_rate = Decimal(1) if holding.currency is Currency.KRW else usd_krw
            positions.append(Position(holding=holding, quote=quote, fx_rate=fx_rate))
        return positions

    def summary(self, positions: list[Position] | None = None) -> AccountSummary:
        positions = self.positions() if positions is None else positions
        return AccountSummary(
            initial_cash=self.repo.initial_cash(),
            cash=self.repo.cash(),
            stock_value=sum((p.value_krw for p in positions), Decimal(0)),
            realized_profit=self.repo.realized_profit(),
        )
