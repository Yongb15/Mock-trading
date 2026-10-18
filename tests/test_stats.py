"""매매 통계(승률·평균 손익·보유 기간·종목별)."""
from datetime import datetime
from decimal import Decimal

from mock_trading.domain import Currency, Side
from mock_trading.stats import trade_stats
from mock_trading.storage import Repository


def repo_with(trades):
    """(날짜, 코드, 매수/매도, 수량, 가격) 목록으로 장부를 만든다. 수수료는 100원 고정."""
    repo = Repository()
    repo.reset_account(Decimal(10_000_000))
    for day, symbol, side, qty, price in trades:
        amount = Decimal(price * qty)
        repo.add_trade(symbol=symbol, name=f"이름{symbol}", side=side, quantity=qty, price=Decimal(price),
                       currency=Currency.KRW, fx_rate=Decimal(1), amount_krw=amount, fee_krw=Decimal(100),
                       executed_at=datetime.fromisoformat(day))
    return repo


B, S = Side.BUY, Side.SELL


def test_per_sell_profit_matches_the_ledger_realized_profit():
    """매도별 손익의 합 = 계좌 요약의 실현 손익(같은 이동평균 규칙)."""
    repo = repo_with([
        ("2026-10-01", "A", B, 10, 1000), ("2026-10-02", "A", B, 5, 1300),
        ("2026-10-05", "A", S, 4, 1500), ("2026-10-06", "B", B, 3, 7000),
        ("2026-10-08", "B", S, 3, 6000), ("2026-10-09", "A", S, 11, 900),
    ])
    stats = trade_stats(repo.trades())
    assert stats.total_profit == repo.realized_profit()
    assert stats.sells == 3


def test_win_rate_averages_best_worst():
    repo = repo_with([
        ("2026-10-01", "A", B, 10, 1000), ("2026-10-03", "A", S, 10, 1200),   # +2000 - 200 수수료
        ("2026-10-01", "B", B, 10, 1000), ("2026-10-11", "B", S, 10, 900),    # -1000 - 200
        ("2026-10-01", "C", B, 1, 1000), ("2026-10-02", "C", S, 1, 1200),     # +200 - 200 = 0
    ])
    stats = trade_stats(repo.trades())
    assert [c.profit_krw for c in stats.closed] == [Decimal(1800), Decimal(-1200), Decimal(0)]
    assert stats.win_rate == Decimal(100) / 3  # 손익 0인 매도는 분모에만
    assert stats.average_win == Decimal(1800) and stats.average_loss == Decimal(-1200)
    assert stats.best.symbol == "A" and stats.worst.symbol == "B"
    assert [c.holding_days for c in stats.closed] == [2, 10, 1]
    assert stats.average_holding_days == Decimal("4.3")


def test_holding_period_restarts_after_selling_everything():
    """전부 판 뒤 다시 사면 보유 기간을 새로 센다. 일부만 팔면 처음 산 날부터."""
    repo = repo_with([
        ("2026-10-01", "A", B, 10, 1000), ("2026-10-04", "A", S, 5, 1000),
        ("2026-10-05", "A", S, 5, 1000), ("2026-10-10", "A", B, 2, 1000), ("2026-10-12", "A", S, 2, 1000),
    ])
    assert [c.holding_days for c in trade_stats(repo.trades()).closed] == [3, 4, 2]


def test_by_symbol_sorted_by_profit_and_return_rate():
    repo = repo_with([
        ("2026-10-01", "A", B, 10, 1000), ("2026-10-02", "A", S, 5, 1100), ("2026-10-03", "A", S, 5, 1100),
        ("2026-10-01", "B", B, 1, 5000), ("2026-10-02", "B", S, 1, 9000),
    ])
    stats = trade_stats(repo.trades())
    assert [(r.symbol, r.sells) for r in stats.by_symbol] == [("B", 1), ("A", 2)]
    b = next(c for c in stats.closed if c.symbol == "B")
    assert b.cost_krw == Decimal(5100) and round(b.return_rate, 2) == Decimal("74.51")


def test_no_sells_yet():
    stats = trade_stats(repo_with([("2026-10-01", "A", B, 1, 1000)]).trades())
    assert stats.sells == 0 and stats.win_rate is None and stats.average_win is None
    assert stats.best is None and stats.by_symbol == [] and stats.total_profit == 0
