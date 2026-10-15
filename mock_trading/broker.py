"""주문 처리. 수량·가격·잔고 검증은 전부 여기서 한다(화면은 입력만 넘긴다)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .domain import (
    MAX_ORDER_QUANTITY,
    USD_KRW_MAX,
    USD_KRW_MIN,
    Currency,
    Position,
    Quote,
    Side,
    Trade,
    TradingError,
    clean_name,
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

# 전일 종가 대비 (하한, 상한) 배수. 벗어나는 시세는 데이터 오류로 보고 주문을 막는다.
# 국내 주식은 하루 가격제한폭이 ±30%라 여유를 둔 0.65~1.35배, 미국 주식은 제한이 없어 1/3~3배.
PRICE_BAND = {
    Currency.KRW: (Decimal("0.65"), Decimal("1.35")),
    Currency.USD: (Decimal(1) / 3, Decimal(3)),
}


# 확인창에서 본 가격과 체결 시 가격이 이만큼(%) 넘게 다르면 다시 확인하게 한다.
PRICE_SLIPPAGE_LIMIT = Decimal(5)


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
        rate = normalize_fx(positive_decimal(self.provider.get_usd_krw(), "환율"))
        if not USD_KRW_MIN <= rate <= USD_KRW_MAX:
            raise TradingError(f"환율이 비정상적입니다({rate:,}원). 잠시 후 다시 시도하세요.")
        return rate

    def _checked_quote(self, symbol: str) -> Quote:
        """provider가 무엇을 돌려주든 체결 전에 한 번 더 확인한다.

        전일 종가가 없으면 시세가 정상인지 판단할 기준이 없으므로 주문을 막는다(fail-closed).
        """
        quote = self.provider.get_quote(normalize_symbol(symbol))
        price = normalize_price(quote.currency, positive_decimal(quote.price, "현재가"))
        if quote.previous_close is None:
            raise TradingError(
                "전일 종가를 확인할 수 없어 시세가 정상인지 판단할 수 없습니다. "
                "매수·매도 모두 잠시 후 다시 시도하세요."
            )
        previous = normalize_price(quote.currency, positive_decimal(quote.previous_close, "전일 종가"))
        low, high = PRICE_BAND[quote.currency]
        if not previous * low <= price <= previous * high:
            raise TradingError("시세가 비정상적으로 크게 변해 주문을 막았습니다. 잠시 후 다시 시도하세요.")
        return Quote(quote.symbol, clean_name(quote.name, quote.symbol), price, quote.currency, previous)

    # ---- 주문 ----
    def buy(self, symbol: str, quantity: int, *, expected_price: Decimal, expected_fx: Decimal | None = None) -> Trade:
        return self._execute(Side.BUY, symbol, quantity, expected_price, expected_fx)

    def sell(self, symbol: str, quantity: int, *, expected_price: Decimal, expected_fx: Decimal | None = None) -> Trade:
        return self._execute(Side.SELL, symbol, quantity, expected_price, expected_fx)

    @staticmethod
    def _check_slippage(actual: Decimal, expected: Decimal | None, what: str) -> None:
        # 확인 값이 없으면 검사를 건너뛰지 않고 주문을 막는다(fail-closed).
        if expected is None:
            raise TradingError(f"주문 전에 확인한 {what}이 없어 주문할 수 없습니다.")
        expected = positive_decimal(expected, f"확인한 {what}")
        if abs(actual - expected) / expected * 100 > PRICE_SLIPPAGE_LIMIT:
            raise TradingError(
                f"확인한 뒤 {what}이 {PRICE_SLIPPAGE_LIMIT}% 넘게 변했습니다. 새 값을 확인하고 다시 주문하세요."
            )

    def _execute(
        self,
        side: Side,
        symbol: str,
        quantity: int,
        expected_price: Decimal | None,
        expected_fx: Decimal | None,
    ) -> Trade:
        """expected_price·expected_fx: 사용자가 확인창에서 본 가격과 환율(원화 종목은 환율 불필요).
        없거나 체결 값과 크게 다르면 체결하지 않는다."""
        self._validate_quantity(quantity)
        self._require_account()
        # 네트워크 조회 중에 계좌가 초기화되면, 이전 계좌에서 낸 주문을 새 계좌에 기록하지 않는다.
        generation = self.repo.account_generation()
        try:
            quote = self._checked_quote(symbol)
            self._check_slippage(quote.price, expected_price, "가격")
            fx_rate = self.fx_rate_for(quote.currency)
            if quote.currency is not Currency.KRW:
                self._check_slippage(fx_rate, expected_fx, "환율")
            amount = krw(quote.price * fx_rate * quantity)
            fee = self.fees.fee(side, quote.currency, amount)
        except InvalidOperation:
            raise TradingError("금액을 계산할 수 없는 시세입니다.") from None
        if amount <= 0:
            raise TradingError("체결 금액이 0원이라 주문할 수 없습니다.")

        # 확인과 기록을 한 트랜잭션에서: 그 사이에 다른 주문이 끼어들 수 없다.
        with self.repo.transaction():
            if self.repo.account_generation() != generation:
                raise TradingError("주문을 처리하는 동안 계좌가 초기화되어 주문을 취소했습니다.")
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
        if unit <= 0:  # 다른 provider가 0을 주더라도 0으로 나누지 않는다
            return 0
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
        """보유 종목을 현재가로 평가한다. 한 종목의 시세가 실패해도 나머지와 계좌 화면은 계속 보여준다."""
        self._require_account()
        holdings = self.repo.holdings()
        usd_krw = None
        if any(h.currency is Currency.USD for h in holdings.values()):
            try:
                usd_krw = self.fx_rate_for(Currency.USD)
            except Exception:
                usd_krw = None
        positions = []
        for holding in holdings.values():
            fx_rate = Decimal(1) if holding.currency is Currency.KRW else usd_krw
            try:
                quote = self.provider.get_quote(holding.symbol) if fx_rate is not None else None
            except Exception:
                quote = None  # 상장폐지·일시 오류: "시세 없음"으로 표시하고 원가로 평가
            positions.append(Position(holding=holding, quote=quote if fx_rate else None, fx_rate=fx_rate))
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
