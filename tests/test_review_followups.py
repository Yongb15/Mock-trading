"""v0.1.0 리뷰 후속 항목(Security 참고 1, QA N1·N2)의 테스트."""
import sqlite3
from decimal import Decimal

import pytest

from mock_trading.broker import Broker
from mock_trading.domain import TradingError
from mock_trading.storage import Repository

from .test_broker import FakeProvider


@pytest.fixture
def broker():
    b = Broker(Repository(), FakeProvider())
    b.open_account(10_000_000)
    return b


def test_order_without_confirmed_price_is_rejected(broker):
    """Security 참고 1: 확인 값이 없으면 검사를 건너뛰지 않고 막는다(fail-closed)."""
    with pytest.raises(TypeError):
        broker.buy("005930", 1)  # expected_price는 필수 키워드 인자
    with pytest.raises(TradingError, match="확인한 가격"):
        broker.buy("005930", 1, expected_price=None)
    assert broker.repo.trades() == []


def test_usd_order_without_confirmed_fx_is_rejected(broker):
    with pytest.raises(TradingError, match="확인한 환율"):
        broker.buy("AAPL", 1, expected_price=Decimal("200.50"))
    trade = broker.buy("AAPL", 1, expected_price=Decimal("200.50"), expected_fx=Decimal("1300"))
    assert trade.fx_rate == Decimal("1300")


def test_krw_order_does_not_need_fx(broker):
    trade = broker.buy("005930", 1, expected_price=Decimal("70000"))
    assert trade.fx_rate == 1


def test_generation_is_unique_random_value():
    """QA N2: 세대 값은 시각이 아니라 uuid라 연달아 초기화해도 겹치지 않는다."""
    repo = Repository()
    seen = set()
    for _ in range(200):
        repo.reset_account(Decimal("1000000"))
        seen.add(repo.account_generation())
    assert len(seen) == 200
    assert all(len(g) == 32 for g in seen)


def test_v010_database_is_migrated(tmp_path):
    """v0.1.0 스키마(generation 칸 없음)로 만든 DB도 열리고, 기존 계좌는 created_at을 세대로 쓴다."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE account (id INTEGER PRIMARY KEY CHECK (id = 1), initial_cash TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO account VALUES (1, '5000000', '2026-10-11T10:00:00.123456')")
    conn.commit()
    conn.close()

    repo = Repository(path)
    assert repo.account_generation() == "2026-10-11T10:00:00.123456"
    assert repo.cash() == Decimal("5000000")
    repo.reset_account(Decimal("1000000"))
    assert len(repo.account_generation()) == 32
