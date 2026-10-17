"""자산 구성 비중(내 계좌 막대)."""
from decimal import Decimal

from mock_trading.allocation import MAX_NAMED, slices
from mock_trading.domain import Currency, Holding, Position


def position(symbol, value, name=None):
    holding = Holding(symbol=symbol, name=name or symbol, currency=Currency.KRW, quantity=1,
                      cost_krw=Decimal(value))
    return Position(holding, None, None)  # 시세가 없으면 원가로 평가 → value_krw == value


def test_shares_and_order():
    result = slices([position("A", 300), position("B", 500)], Decimal(200))
    assert [(s.key, s.share) for s in result] == [("B", Decimal("50.0")), ("A", Decimal("30.0")), ("현금", Decimal("20.0"))]
    assert sum(s.value for s in result) == Decimal(1000)


def test_color_follows_entity_not_rank():
    """값이 바뀌어 순서가 뒤집혀도 같은 종목은 같은 색(처음 산 순서로 슬롯)."""
    before = {s.key: s.slot for s in slices([position("A", 300), position("B", 500)], Decimal(0))}
    after = {s.key: s.slot for s in slices([position("A", 900), position("B", 500)], Decimal(0))}
    assert before == after == {"A": 0, "B": 1}


def test_tail_folds_into_other_and_cash_and_others_are_gray():
    many = [position(f"S{i}", 100 + i) for i in range(MAX_NAMED + 3)]
    result = slices(many, Decimal(50))
    named = [s for s in result if s.slot is not None]
    assert len(named) == MAX_NAMED and sorted(s.slot for s in named) == list(range(MAX_NAMED))
    other = next(s for s in result if s.key == "기타")
    assert other.label == "기타 3종목" and other.slot is None
    assert result[-1].key == "현금" and result[-1].slot is None


def test_empty_and_zero_values():
    assert slices([], Decimal(0)) == []
    assert [s.key for s in slices([], Decimal(1000))] == ["현금"]
    assert [s.key for s in slices([position("A", 0)], Decimal(10))] == ["현금"]
