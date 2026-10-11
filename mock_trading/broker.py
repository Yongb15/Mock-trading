"""주문 처리. 수량·가격·잔고 검증은 전부 여기서 한다(화면은 입력만 넘긴다)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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
    normalize_fx,
    normalize_price,
    positive_decimal,
)
from .market_data import QuoteProvider, normalize_symbol
from .storage import Repository

MIN_INITIAL_CASH = Decimal("100000")
MAX_INITIAL_CASH = Decimal("100000000000")  # 1000억

# 전일 종가 대비 이 배수를 벗어나는 시세는 데이터 오류로 보고 주문을 막는다.
# 국내 주식은 하루 ±30% 제한이 있고, 미국 주식도 하루에 3배 넘게 움직이는 일은 드물다.
SUSPICIOUS_PRICE_RATIO = Decimal(3)


@dataclass(frozen=True)
class FeePolicy:
    """실제 증권사 수수료·세금을 단순화한 값. 연습용이므로 필요하면 바꿔서 쓴다.

    - 수수료와 세금을 합친 요율로 한 번 계산하고 원 단위 미만은 버린다(실제는 각각 절사, 차이 1원 이내).
      그래서 아주 작은 주문은 수수료가 0원일 수 있다.
    - 거래세는 원화 종목 매도에 모두 붙인다. 실제로는 ETF 등 면제 종목이 있다.
    """
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
        if isinstance(initial_cash, bool):
            raise TradingError("기초 자금은 숫자여야 합니다.")
        try:
            amount = Decimal(str(initial_cash))
        except InvalidOperation:
            raise TradingError("기초 자금은 숫자여야 합니다.") from None
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise TradingError("기초 자금은 원 단위 정수여야 합니다.")
        if not MIN_INITIAL_CASH <= amount <= MAX_INITIAL_CASH:
            raise TradingError(f"기초 자금은 {MIN_INITIAL_CASH:,}원 ~ {MAX_INITIAL_CASH:,}원 사이여야 합니다.")
        # "1E+5", "100000.0" 같은 표기를 "100000"으로 맞춰 저장한다.
        self.repo.reset_account(amount.quantize(Decimal(1)))

    def _require_account(self) -> None:
        if not self.repo.has_account():
            raise TradingError("먼저 기초 자금을 받아 계좌를 만드세요.")

    def fx_rate_for(self, currency: Currency) -> Decimal:
        if currency is Currency.KRW:
            return Decimal(1)
        return normalize_fx(positive_decimal(self.provider.get_usd_krw(), "환율"))

    def _checked_quote(self, symbol: str) -> Quote:
        """provider가 무엇을 돌려주든 체결 전에 한 번 더 확인한다."""
        quote = self.provider.get_quote(normalize_symbol(symbol))
        price = normalize_price(quote.currency, positive_decimal(quote.price, "현재가"))
        previous = quote.previous_close
        if previous is not None and previous > 0:
            if price > previous * SUSPICIOUS_PRICE_RATIO or price * SUSPICIOUS_PRICE_RATIO < previous:
                raise TradingError("시세가 비정상적으로 크게 변해 주문을 막았습니다. 잠시 후 다시 시도하세요.")
        return Quote(quote.symbol, quote.name, price, quote.currency, previous)

    # ---- 주문 ----
    def buy(self, symbol: str, quantity: int) -> Trade:
        return self._execute(Side.BUY, symbol, quantity)

    def sell(self, symbol: str, quantity: int) -> Trade:
        return self._execute(Side.SELL, symbol, quantity)

    def _execute(self, side: Side, symbol: str, quantity: int) -> Trade:
        self._validate_quantity(quantity)
        self._require_account()
        try:
            quote = self._checked_quote(symbol)
            fx_rate = self.fx_rate_for(quote.currency)
            amount = krw(quote.price * fx_rate * quantity)
            fee = self.fees.fee(side, quote.currency, amount)
        except InvalidOperation:
            raise TradingError("금액을 계산할 수 없는 시세입니다.") from None
        if amount <= 0:
            raise TradingError("체결 금액이 0원이라 주문할 수 없습니다.")

        # 확인과 기록을 한 트랜잭션에서: 그 사이에 다른 주문이 끼어들 수 없다.
        with self.repo.transaction():
            ledger = self.repo.ledger()
            if side is Side.BUY:
                if amount + fee > ledger.cash:
                    raise TradingError(f"현금이 부족합니다. 필요 {amount + fee:,}원 / 보유 {ledger.cash:,}원")
            else:
                held = ledger.holdings.get(quote.symbol)
                held_quantity = held.quantity if held else 0
                if quantity > held_quantity:
                    raise TradingError(f"보유 수량이 부족합니다. 주문 {quantity}주 / 보유 {held_quantity}주")
            return self.repo.add_trade(
                symbol=quote.symbol,
                name=quote.name,
                side=side,
                quantity=quantity,
                price=quote.price,
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
        """현재 현금으로 살 수 있는 최대 수량 (수수료 포함).

        수수료가 0 이상이므로 cash / 단가가 상한이다. 거기서부터 실제 체결 규칙으로 계산해 내려온다.
        """
        self._require_account()
        unit = quote.price * self.fx_rate_for(quote.currency)
        cash = self.repo.cash()
        quantity = min(int(cash / unit), MAX_ORDER_QUANTITY)
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
        usd_krw = self.fx_rate_for(Currency.USD) if any(h.currency is Currency.USD for h in holdings.values()) else None
        positions = []
        for holding in holdings.values():
            quote = self.provider.get_quote(holding.symbol)
            fx_rate = Decimal(1) if holding.currency is Currency.KRW else usd_krw
            positions.append(Position(holding=holding, quote=quote, fx_rate=fx_rate))
        return positions

    def summary(self, positions: list[Position] | None = None) -> AccountSummary:
        self._require_account()
        positions = self.positions() if positions is None else positions
        ledger = self.repo.ledger()
        return AccountSummary(
            initial_cash=self.repo.initial_cash(),
            cash=ledger.cash,
            stock_value=sum((p.value_krw for p in positions), Decimal(0)),
            realized_profit=ledger.realized_profit,
        )
