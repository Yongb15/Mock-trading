"""종목 이름 검색: "삼성", "ㅅㅅㅈㅈ", "애플"처럼 이름으로 찾아 코드로 바꾼다.

무료 시세에는 이름 검색 API가 없어서, 시장 순위에 쓰는 대형주 목록(market_rank.UNIVERSE)을 사전으로 쓴다.
목록에 없는 종목은 지금처럼 코드(005930, AAPL)로 조회하면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .market_rank import UNIVERSE

# 미국 종목은 한글로도 찾을 수 있게(국내 증권 앱에서 흔히 쓰는 이름)
KOREAN_ALIASES = {
    "AAPL": ("애플",), "MSFT": ("마이크로소프트",), "NVDA": ("엔비디아",), "GOOGL": ("알파벳", "구글"),
    "AMZN": ("아마존",), "META": ("메타",), "TSLA": ("테슬라",), "AVGO": ("브로드컴",),
    "BRK-B": ("버크셔 해서웨이",), "JPM": ("JP모건",), "V": ("비자",), "MA": ("마스터카드",),
    "UNH": ("유나이티드헬스",), "XOM": ("엑슨모빌",), "LLY": ("일라이 릴리",), "JNJ": ("존슨앤드존슨",),
    "WMT": ("월마트",), "PG": ("프록터앤드갬블",), "HD": ("홈디포",), "COST": ("코스트코",),
    "NFLX": ("넷플릭스",), "AMD": ("에이엠디",), "INTC": ("인텔",), "ORCL": ("오라클",),
    "CRM": ("세일즈포스",), "ADBE": ("어도비",), "KO": ("코카콜라",), "PEP": ("펩시코",),
    "DIS": ("디즈니",), "NKE": ("나이키",), "BA": ("보잉",), "PLTR": ("팔란티어",),
}
MAX_RESULTS = 8

_CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def _choseong(text: str) -> str:
    """한글 음절을 초성으로(삼성전자 → ㅅㅅㅈㅈ). 한글이 아닌 글자는 그대로."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        out.append(_CHOSEONG[code // 588] if 0 <= code < 11172 else ch)
    return "".join(out)


def _key(text: str) -> str:
    """비교용: 공백·대소문자 무시."""
    return "".join(text.split()).casefold()


@dataclass(frozen=True)
class Match:
    symbol: str
    name: str

    @property
    def label(self) -> str:
        """자동 완성 목록에 보이는 글자: "삼성전자  005930.KS"."""
        return f"{self.name}  {self.symbol}"


def _entries() -> list[tuple[str, str, tuple[str, ...]]]:
    """(코드, 대표 이름, 검색할 이름들)."""
    entries = []
    for market in UNIVERSE.values():
        for symbol, name in market:
            entries.append((symbol, name, (name, *KOREAN_ALIASES.get(symbol, ()))))
    return entries


_ENTRIES = _entries()


def search(query: str, limit: int = MAX_RESULTS) -> list[Match]:
    """이름(별칭·초성 포함) 또는 코드 앞부분으로 찾는다. 정확히 같음 → 앞부분 일치 → 포함 순."""
    key = _key(query)
    if not key:
        return []
    only_choseong = all(ch in _CHOSEONG for ch in key)
    scored = []
    for order, (symbol, name, names) in enumerate(_ENTRIES):
        best = None
        for candidate in names:
            target = _key(_choseong(candidate) if only_choseong else candidate)
            if target == key:
                score = 0
            elif target.startswith(key):
                score = 1
            elif key in target:
                score = 2
            else:
                continue
            best = score if best is None else min(best, score)
        code = symbol.casefold()
        if code == key or code.split(".")[0] == key:
            best = 0
        elif best is None and code.startswith(key):
            best = 3
        if best is not None:
            scored.append((best, order, Match(symbol, name)))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [match for _, _, match in scored[:limit]]


def resolve(text: str) -> str:
    """조회창에 친 글자를 조회할 코드로. 이름과 정확히 같거나 목록에서 고른 "이름  코드"면 그 코드,
    아니면 입력 그대로(코드로 보고 시세 조회에서 검증한다)."""
    stripped = text.strip()
    for symbol, name, names in _ENTRIES:
        # 목록 라벨은 글자 그대로 같을 때만(이름을 고친 "애플  005930.KS"가 삼성전자로 바뀌지 않게, Mock_Sequrity NS1)
        if stripped == Match(symbol, name).label or any(_key(n) == _key(stripped) for n in names):
            return symbol
    return stripped
