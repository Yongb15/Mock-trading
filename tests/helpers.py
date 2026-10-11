"""테스트 도우미.

실제 Broker는 주문할 때 확인창에서 본 가격·환율(expected_price·expected_fx)을 반드시 받는다.
가격 변동 검사가 주제가 아닌 테스트에서는 AutoConfirmBroker가 지금 시세를 확인 값으로 채워 준다.
"""
from decimal import Decimal

from mock_trading.broker import Broker
from mock_trading.domain import Currency
from mock_trading.market_data import normalize_symbol

# 시세를 못 가져오는 경우의 자리 표시 값. 실제 경로가 시세 오류를 먼저 내므로 이 값은 비교에 쓰이지 않는다.
_PLACEHOLDER = Decimal(1)


class AutoConfirmBroker(Broker):
    def _confirmed(self, symbol, expected_price, expected_fx):
        try:
            quote = self.provider.get_quote(normalize_symbol(symbol))
        except Exception:
            return expected_price or _PLACEHOLDER, expected_fx
        if expected_price is None:
            expected_price = quote.price
        if expected_fx is None and quote.currency is Currency.USD:
            try:
                expected_fx = self.fx_rate_for(Currency.USD)
            except Exception:
                expected_fx = _PLACEHOLDER
        return expected_price, expected_fx

    def buy(self, symbol, quantity, *, expected_price=None, expected_fx=None):
        expected_price, expected_fx = self._confirmed(symbol, expected_price, expected_fx)
        return super().buy(symbol, quantity, expected_price=expected_price, expected_fx=expected_fx)

    def sell(self, symbol, quantity, *, expected_price=None, expected_fx=None):
        expected_price, expected_fx = self._confirmed(symbol, expected_price, expected_fx)
        return super().sell(symbol, quantity, expected_price=expected_price, expected_fx=expected_fx)
