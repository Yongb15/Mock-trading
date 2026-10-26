"""매매 일지(거래 메모)."""
import sqlite3
from datetime import datetime
from decimal import Decimal

import pytest

from mock_trading.domain import MAX_NOTE_LENGTH, Currency, Side, TradingError, clean_note
from mock_trading.storage import Repository


def repo_with_trade():
    repo = Repository()
    repo.reset_account(Decimal(1_000_000))
    trade = repo.add_trade(symbol="A", name="알파", side=Side.BUY, quantity=1, price=Decimal(1000),
                           currency=Currency.KRW, fx_rate=Decimal(1), amount_krw=Decimal(1000), fee_krw=Decimal(0),
                           executed_at=datetime(2026, 10, 1))
    return repo, trade


def test_set_update_and_clear_note():
    repo, trade = repo_with_trade()
    assert repo.set_note(trade.id, "  실적 기대  ") == "실적 기대"
    assert repo.notes() == {trade.id: "실적 기대"}
    repo.set_note(trade.id, "생각보다 비쌌음")
    assert repo.notes() == {trade.id: "생각보다 비쌌음"}
    repo.set_note(trade.id, "   ")
    assert repo.notes() == {}


def test_note_is_cleaned_to_one_line_and_limited():
    assert clean_note("첫 줄\n둘째 줄\t탭") == "첫 줄 둘째 줄 탭"
    assert clean_note("\x00\x1b[31m빨강​") == "[31m빨강"
    assert len(clean_note("가" * 500)) == MAX_NOTE_LENGTH
    with pytest.raises(TradingError):
        clean_note(None)


def test_note_needs_an_existing_trade_and_valid_id():
    repo, _ = repo_with_trade()
    with pytest.raises(TradingError):
        repo.set_note(999, "없는 거래")
    with pytest.raises(TradingError):
        repo.set_note(True, "bool은 번호가 아님")


def test_reset_clears_notes_and_ledger_is_untouched():
    """메모는 장부(trades)를 바꾸지 않고, 계좌 초기화 때 함께 지워진다."""
    repo, trade = repo_with_trade()
    before = repo.trades()
    repo.set_note(trade.id, "메모")
    assert repo.trades() == before
    repo.reset_account(Decimal(1_000_000))
    assert repo.notes() == {}


def test_old_database_without_notes_table_gets_it(tmp_path):
    """v0.7.0 이전 DB에는 trade_notes 표가 없다 → 열 때 만들어지고 기존 거래는 그대로."""
    path = tmp_path / "old.db"
    repo, trade = repo_with_trade()
    repo.close()
    old = Repository(path)
    old.reset_account(Decimal(1_000_000))
    old.add_trade(symbol="A", name="알파", side=Side.BUY, quantity=1, price=Decimal(1000), currency=Currency.KRW,
                  fx_rate=Decimal(1), amount_krw=Decimal(1000), fee_krw=Decimal(0))
    old.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE trade_notes")
    conn.commit()
    conn.close()
    reopened = Repository(path)
    [kept] = reopened.trades()
    reopened.set_note(kept.id, "예전 거래에도 메모")
    assert reopened.notes() == {kept.id: "예전 거래에도 메모"}
    reopened.close()


def test_database_rejects_empty_or_too_long_note():
    """마지막 방어선: DB 제약으로 빈 메모·200자 초과는 저장되지 않는다."""
    repo, trade = repo_with_trade()
    for bad in ("", "가" * 201):
        with pytest.raises(sqlite3.IntegrityError):
            repo._conn.execute("INSERT INTO trade_notes VALUES (?, ?, ?)", (trade.id, bad, "2026-10-01"))
