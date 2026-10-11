"""관심 종목: 자주 보는 종목을 담아 두고 시세를 한 번에 본다.

종목은 시세 조회로 확인된 것만 담는다(없는 코드나 엉뚱한 문자열이 저장되지 않게).
"""
from __future__ import annotations

from dataclasses import dataclass

from .domain import Quote
from .market_data import QuoteProvider, normalize_symbol
from .storage import Repository, WatchItem

MAX_WATCH_ITEMS = 50


@dataclass(frozen=True)
class WatchRow:
    """화면 한 줄. 시세를 못 가져오면 quote가 None."""
    item: WatchItem
    quote: Quote | None


class Watchlist:
    def __init__(self, repo: Repository, provider: QuoteProvider) -> None:
        self.repo = repo
        self.provider = provider

    def add(self, symbol: str) -> Quote:
        """종목을 담고 확인된 시세를 돌려준다. 이미 담겨 있어도 오류 없이 시세만 돌려준다."""
        quote = self.provider.get_quote(normalize_symbol(symbol))
        self.repo.add_watch(quote.symbol, quote.name, quote.currency, limit=MAX_WATCH_ITEMS)
        return quote

    def remove(self, symbol: str) -> bool:
        return self.repo.remove_watch(normalize_symbol(symbol))

    def contains(self, symbol: str) -> bool:
        return self.repo.is_watched(normalize_symbol(symbol))

    def rows(self) -> list[WatchRow]:
        """담긴 종목의 현재 시세. 한 종목이 실패해도 나머지는 보여준다."""
        rows = []
        for item in self.repo.watchlist():
            try:
                quote = self.provider.get_quote(item.symbol)
            except Exception:
                quote = None
            rows.append(WatchRow(item, quote))
        return rows
