"""종목 이름 검색."""
from mock_trading.symbols import resolve, search


def symbols_of(query):
    return [m.symbol for m in search(query)]


def test_name_prefix_and_contains():
    assert symbols_of("삼성전자")[0] == "005930.KS"  # 정확히 같은 이름이 맨 앞
    found = symbols_of("삼성")
    assert "005930.KS" in found and "028260.KS" in found  # 삼성전자, 삼성물산
    assert symbols_of("하이닉스") == ["000660.KS"]  # 가운데 포함


def test_choseong_and_korean_alias_and_case():
    assert symbols_of("ㅅㅅㅈㅈ")[0] == "005930.KS"
    assert symbols_of("애플") == ["AAPL"]
    assert symbols_of("구글") == ["GOOGL"]
    assert symbols_of("tesla") == ["TSLA"]
    assert symbols_of(" 삼성 전자 ")[0] == "005930.KS"  # 공백 무시


def test_code_search():
    assert symbols_of("005930")[0] == "005930.KS"
    assert symbols_of("aapl")[0] == "AAPL"


def test_limit_and_empty():
    assert len(search("삼성", limit=3)) == 3
    assert search("") == [] and search("   ") == []
    assert search("없는종목이름xyz") == []


def test_resolve_name_label_or_code():
    assert resolve("삼성전자") == "005930.KS"
    assert resolve("애플") == "AAPL"
    assert resolve("삼성전자  005930.KS") == "005930.KS"  # 목록에서 고른 글자
    assert resolve(" 005930 ") == "005930"  # 코드는 그대로(시세 조회가 검증)
    assert resolve("삼성") == "삼성"  # 정확하지 않은 이름은 바꾸지 않음


def test_edited_label_is_not_resolved_to_its_code():
    """Mock_Sequrity NS1: 라벨의 이름 부분을 고치면 끝의 코드로 바꾸지 않는다(그대로 코드 검증으로 넘어가 실패)."""
    assert resolve("애플  005930.KS") == "애플  005930.KS"
    assert resolve("아무거나  V") == "아무거나  V"
    for match in search("삼성", limit=20):
        assert resolve(match.label) == match.symbol
