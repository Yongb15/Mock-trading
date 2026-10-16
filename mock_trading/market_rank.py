"""시장 순위: 오늘 많이 오른 종목·많이 내린 종목·거래가 많은 종목.

무료 시세(yfinance)로는 한국 시장 전체 순위를 받을 수 없고, 미국 순위 조회도 정렬이 믿을 만하지 않았다.
그래서 **미리 정한 대형주 목록** 안에서 직접 순위를 계산한다(화면에 그렇게 알린다).
목록은 한 번에 일괄 조회하고, 요청 제한에 걸리지 않게 결과를 잠시 기억해 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from .domain import Currency

# 같은 시장을 다시 받기까지의 최소 간격. 목록이 길어(시장당 30~40종목) 1분마다 받으면 요청 제한에 걸리기 쉽다.
CACHE_TTL = timedelta(minutes=5)
TOP_N = 20


class Market(str, Enum):
    KR = "KR"
    US = "US"


class RankBy(str, Enum):
    GAINERS = "GAINERS"   # 상승률 높은 순
    LOSERS = "LOSERS"     # 하락률 높은 순
    VOLUME = "VOLUME"     # 거래량 많은 순


# (코드, 이름). 한국은 코스피 .KS / 코스닥 .KQ 접미사까지 붙여 둔다.
UNIVERSE: dict[Market, list[tuple[str, str]]] = {
    Market.KR: [
        ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"), ("373220.KS", "LG에너지솔루션"),
        ("207940.KS", "삼성바이오로직스"), ("005380.KS", "현대차"), ("000270.KS", "기아"),
        ("068270.KS", "셀트리온"), ("005490.KS", "POSCO홀딩스"), ("035420.KS", "NAVER"),
        ("035720.KS", "카카오"), ("051910.KS", "LG화학"), ("006400.KS", "삼성SDI"),
        ("105560.KS", "KB금융"), ("055550.KS", "신한지주"), ("086790.KS", "하나금융지주"),
        ("012330.KS", "현대모비스"), ("028260.KS", "삼성물산"), ("066570.KS", "LG전자"),
        ("003550.KS", "LG"), ("096770.KS", "SK이노베이션"), ("017670.KS", "SK텔레콤"),
        ("030200.KS", "KT"), ("034730.KS", "SK"), ("032830.KS", "삼성생명"),
        ("015760.KS", "한국전력"), ("009150.KS", "삼성전기"), ("018260.KS", "삼성에스디에스"),
        ("011200.KS", "HMM"), ("012450.KS", "한화에어로스페이스"), ("329180.KS", "HD현대중공업"),
        ("042660.KS", "한화오션"), ("259960.KS", "크래프톤"), ("323410.KS", "카카오뱅크"),
        ("377300.KS", "카카오페이"), ("247540.KQ", "에코프로비엠"), ("086520.KQ", "에코프로"),
        ("196170.KQ", "알테오젠"), ("028300.KQ", "HLB"),
    ],
    Market.US: [
        ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"), ("GOOGL", "Alphabet"),
        ("AMZN", "Amazon"), ("META", "Meta"), ("TSLA", "Tesla"), ("AVGO", "Broadcom"),
        ("BRK-B", "Berkshire Hathaway"), ("JPM", "JPMorgan Chase"), ("V", "Visa"), ("MA", "Mastercard"),
        ("UNH", "UnitedHealth"), ("XOM", "Exxon Mobil"), ("LLY", "Eli Lilly"), ("JNJ", "Johnson & Johnson"),
        ("WMT", "Walmart"), ("PG", "Procter & Gamble"), ("HD", "Home Depot"), ("COST", "Costco"),
        ("NFLX", "Netflix"), ("AMD", "AMD"), ("INTC", "Intel"), ("ORCL", "Oracle"),
        ("CRM", "Salesforce"), ("ADBE", "Adobe"), ("KO", "Coca-Cola"), ("PEP", "PepsiCo"),
        ("DIS", "Disney"), ("NKE", "Nike"), ("BA", "Boeing"), ("PLTR", "Palantir"),
    ],
}
CURRENCY = {Market.KR: Currency.KRW, Market.US: Currency.USD}


@dataclass(frozen=True)
class RankRow:
    rank: int
    symbol: str
    name: str
    currency: Currency
    price: Decimal
    change_rate: Decimal | None   # 전일 대비 %
    volume: int | None


@dataclass(frozen=True)
class Ranking:
    market: Market
    by: RankBy
    rows: list[RankRow]
    universe_size: int   # 목록 종목 수
    priced: int          # 그중 시세를 받은 종목 수
    fetched_at: datetime


def rank(snapshots: list, by: RankBy, *, top: int = TOP_N) -> list[RankRow]:
    """순수 함수: (시세, 거래량) 목록을 기준에 맞춰 정렬해 위에서 top개.

    등락률·거래량을 모르는 종목은 그 기준의 순위에서 뺀다(맨 아래에 0으로 끼워 넣지 않는다).
    """
    if by is RankBy.VOLUME:
        usable = [s for s in snapshots if s.volume is not None]
        usable.sort(key=lambda s: (-s.volume, s.quote.symbol))
    else:
        usable = [s for s in snapshots if s.quote.change_rate is not None]
        sign = -1 if by is RankBy.GAINERS else 1
        usable.sort(key=lambda s: (sign * s.quote.change_rate, s.quote.symbol))
        if by is RankBy.GAINERS:
            usable = [s for s in usable if s.quote.change_rate > 0]   # 오른 종목만 "상승"에
        else:
            usable = [s for s in usable if s.quote.change_rate < 0]   # 내린 종목만 "하락"에
    return [
        RankRow(i + 1, s.quote.symbol, s.quote.name, s.quote.currency, s.quote.price, s.quote.change_rate, s.volume)
        for i, s in enumerate(usable[:top])
    ]


class MarketRanking:
    """시장별 시세를 기억해 두고(CACHE_TTL), 기준을 바꿀 때는 다시 받지 않고 정렬만 다시 한다."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self._cache: dict[Market, tuple[datetime, list]] = {}

    def ranking(self, market: Market, by: RankBy, *, now: datetime | None = None, force: bool = False) -> Ranking:
        now = now or datetime.now()
        cached = self._cache.get(market)
        if force or cached is None or now - cached[0] >= CACHE_TTL:
            items = [(symbol, name, CURRENCY[market]) for symbol, name in UNIVERSE[market]]
            snapshots = self._fetch(items)
            if snapshots or cached is None:  # 이번에 하나도 못 받았으면 기억해 둔 값을 계속 쓴다
                cached = (now, snapshots)
                self._cache[market] = cached
        fetched_at, snapshots = cached
        return Ranking(market, by, rank(snapshots, by), len(UNIVERSE[market]), len(snapshots), fetched_at)

    def _fetch(self, items: list[tuple[str, str, Currency]]) -> list:
        get_snapshots = getattr(self.provider, "get_snapshots", None)
        if get_snapshots is not None:
            return list(get_snapshots(items).values())
        # 일괄 조회가 없는 시세 출처: 종목별로(거래량은 모름)
        from .market_data import Snapshot

        snapshots = []
        for symbol, _name, _currency in items:
            try:
                snapshots.append(Snapshot(self.provider.get_quote(symbol), None))
            except Exception:
                continue
        return snapshots
