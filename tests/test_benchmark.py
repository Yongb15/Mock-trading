"""시장(지수 ETF)과 수익률 비교."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from mock_trading.history import BENCHMARKS, DailyCloseCache, benchmark_returns, load_benchmarks
from mock_trading.market_data import is_valid_symbol

D = [date(2026, 10, 1) + timedelta(days=i) for i in range(5)]  # 10/1(목)~10/5(월)


def test_benchmark_symbols_pass_the_symbol_rule():
    """지수 기호(^KS11) 대신 ETF 코드를 쓰는 이유: 외부로 나가는 코드는 모두 종목 코드 규칙을 지킨다."""
    assert all(is_valid_symbol(symbol) for symbol, _, _ in BENCHMARKS)


def test_returns_start_at_zero_and_fill_holidays():
    closes = {"069500.KS": {D[0]: Decimal(100), D[1]: Decimal(110), D[4]: Decimal(90)}}
    [kospi] = benchmark_returns(D, closes, {})
    assert kospi.name == "코스피200"
    assert [r for _, r in kospi.returns] == [Decimal(0), Decimal(10), Decimal(10), Decimal(10), Decimal(-10)]


def test_usd_benchmark_is_converted_to_krw():
    """달러 ETF는 원화 환산: 가격이 그대로여도 환율이 오르면 원화 수익률이 오른다."""
    closes = {"SPY": {D[0]: Decimal(500), D[1]: Decimal(500)}}
    fx = {D[0]: Decimal(1400), D[1]: Decimal(1470)}
    [spy] = benchmark_returns(D[:2], closes, fx)
    assert spy.name == "S&P500" and spy.returns[-1][1] == Decimal(5)


def test_benchmark_without_a_base_value_is_left_out():
    """기준일 값(직전 거래일 포함)이 없으면 비교가 틀어지므로 그 지수는 그리지 않는다."""
    closes = {"069500.KS": {D[2]: Decimal(100)}, "SPY": {D[0]: Decimal(500)}}  # SPY는 환율이 없음
    assert benchmark_returns(D, closes, {}) == []


def test_earlier_close_counts_as_base_value():
    closes = {"069500.KS": {D[0] - timedelta(days=3): Decimal(200), D[2]: Decimal(220)}}
    [kospi] = benchmark_returns(D, closes, {})
    assert kospi.returns[0][1] == 0 and kospi.returns[2][1] == Decimal(10)


class Provider:
    def __init__(self):
        self.calls = []

    def get_daily_closes(self, symbols, start, *, include_usd_krw):
        self.calls.append((tuple(symbols), start, include_usd_krw))
        closes = {s: {start: Decimal(100), start + timedelta(days=20): Decimal(120)} for s in symbols}
        return closes, ({start: Decimal(1400)} if include_usd_krw else {})


def test_load_uses_the_cache_and_one_request():
    provider, cache = Provider(), DailyCloseCache()
    now = datetime(2026, 10, 18, 10, 0)
    first = load_benchmarks(provider, D[0], D[0] + timedelta(days=17), cache, now)
    again = load_benchmarks(provider, D[0], D[0] + timedelta(days=17), cache, now + timedelta(minutes=1))
    assert len(provider.calls) == 1 and provider.calls[0][2] is True  # 달러 ETF 때문에 환율도 함께
    assert [s.name for s in first] == ["코스피200", "S&P500"] and first == again


def test_no_provider_support_or_future_start():
    assert load_benchmarks(object(), D[0], D[1], DailyCloseCache()) == []
    assert load_benchmarks(Provider(), D[1], D[0], DailyCloseCache()) == []
