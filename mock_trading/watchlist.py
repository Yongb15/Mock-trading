"""관심 종목: 자주 보는 종목을 담아 두고 시세를 한 번에 본다.

종목은 시세 조회로 확인된 것만 담는다(없는 코드나 엉뚱한 문자열이 저장되지 않게).
"""
from __future__ import annotations

from dataclasses import dataclass

from .domain import Quote, clean_name
from .market_data import QuoteProvider, normalize_symbol, symbol_candidates
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
        # provider가 무엇을 주든 저장 전에 한 번 더 정리한다(다른 시세 출처를 붙여도 안전하게).
        name = clean_name(quote.name, quote.symbol)
        self.repo.add_watch(quote.symbol, name, quote.currency, limit=MAX_WATCH_ITEMS)
        return quote

    def remove(self, symbol: str) -> bool:
        """"005930"처럼 시장 접미사 없는 코드도 받는다. 형식이 이상한 저장값도 그대로 지울 수 있다."""
        try:
            candidates = symbol_candidates(symbol)
        except Exception:
            candidates = [symbol] if isinstance(symbol, str) else []
        return self.repo.remove_watch(*candidates)

    def contains(self, symbol: str) -> bool:
        return self.repo.is_watched(*symbol_candidates(symbol))

    def rows(self) -> list[WatchRow]:
        """담긴 종목의 현재 시세. 가능하면 요청 한 번으로 받고, 실패한 종목은 "시세 없음"으로 둔다."""
        items = self.repo.watchlist()
        get_quotes = getattr(self.provider, "get_quotes", None)
        if get_quotes is not None:
            try:
                quotes = get_quotes([(i.symbol, i.name, i.currency) for i in items])
            except Exception:
                quotes = {}
            return [WatchRow(item, quotes.get(item.symbol)) for item in items]
        rows = []
        for item in items:
            try:
                quote = self.provider.get_quote(item.symbol)
            except Exception:
                quote = None
            rows.append(WatchRow(item, quote))
        return rows
