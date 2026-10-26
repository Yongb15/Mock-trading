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


def test_every_public_repository_method_that_touches_the_connection_is_locked():
    """Mock_QA·Mock_Sequrity TN1: 메서드를 끼워 넣다 데코레이터가 밀려 trades()가 잠금을 잃었던 회귀 방지.
    SQLite 연결을 쓰는 공개 메서드는 모두 같은 RLock(_locked)으로 감싸져야 한다."""
    import inspect

    unlocked = []
    for name, member in inspect.getmembers(Repository, inspect.isfunction):
        if name.startswith("_"):
            continue
        body = inspect.getsource(getattr(member, "__wrapped__", member))
        if "_conn" in body and not hasattr(member, "__wrapped__"):
            unlocked.append(name)
    assert unlocked == []


def test_reading_trades_waits_for_a_running_transaction():
    """다른 스레드가 트랜잭션 중이면 trades()는 기다렸다가 커밋된 것만 본다(롤백될 거래를 보지 않는다)."""
    import threading

    repo = Repository(check_same_thread=False)
    repo.reset_account(Decimal(1_000_000))
    inside, release, seen = threading.Event(), threading.Event(), []

    def writer():
        try:
            with repo.transaction():
                repo.add_trade(symbol="A", name="알파", side=Side.BUY, quantity=1, price=Decimal(1000),
                               currency=Currency.KRW, fx_rate=Decimal(1), amount_krw=Decimal(1000),
                               fee_krw=Decimal(0))
                inside.set()
                release.wait(3)
                raise RuntimeError("체결 취소 → 롤백")
        except RuntimeError:
            pass

    thread = threading.Thread(target=writer)
    thread.start()
    inside.wait(3)
    reader = threading.Thread(target=lambda: seen.append(len(repo.trades())))
    reader.start()
    reader.join(0.3)
    assert reader.is_alive()  # 트랜잭션이 끝날 때까지 기다린다
    release.set()
    thread.join(3)
    reader.join(3)
    assert seen == [0]
