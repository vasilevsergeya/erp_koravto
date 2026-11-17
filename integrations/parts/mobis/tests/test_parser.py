import pytest

from mobis_api.parser import MobisBlockedError, parse_max_page, parse_price, parse_stores


def test_parse_price_table() -> None:
    html = """
    <table>
      <tr><th>부품번호</th><th>한글명</th><th>영문명</th><th>가격</th></tr>
      <tr>
        <td>338002F600</td>
        <td>인젝터 어셈블리-연료</td>
        <td>INJECTOR ASSY-FUEL</td>
        <td>272,800원</td>
      </tr>
    </table>
    """

    parsed = parse_price(html, "338002F600")

    assert parsed is not None
    assert parsed.part_number == "338002F600"
    assert parsed.korean_name == "인젝터 어셈블리-연료"
    assert parsed.english_name == "INJECTOR ASSY-FUEL"
    assert parsed.price == "272800"


def test_parse_price_mobis_list_markup() -> None:
    html = """
    <input type="hidden" id="black" name="black" value="N" />
    <ul class="table-d">
      <li role="row">
        <div><span class="t-td"><a href="/simple_search_inventory.do?hkgb=H&amp;ptno=338002F600">338002F600</a></span></div>
        <div><span class="t-td">인젝터 어셈블리-연료</span></div>
        <div><span class="t-td">INJECTOR ASSY-FUEL</span></div>
        <div><span class="t-td">272,800 원</span></div>
      </li>
    </ul>
    """

    parsed = parse_price(html, "338002F600")

    assert parsed is not None
    assert parsed.korean_name == "인젝터 어셈블리-연료"
    assert parsed.english_name == "INJECTOR ASSY-FUEL"
    assert parsed.price == "272800"


def test_parse_price_keeps_single_word_english_name() -> None:
    html = """
    <input type="hidden" id="black" name="black" value="N" />
    <ul class="table-d">
      <li role="row">
        <div><span class="t-td">253975A001</span></div>
        <div><span class="t-td">쉬라우드-라디에이터</span></div>
        <div><span class="t-td">SHROUD</span></div>
        <div><span class="t-td">17,930 원</span></div>
      </li>
    </ul>
    """

    parsed = parse_price(html, "253975A001")

    assert parsed is not None
    assert parsed.korean_name == "쉬라우드-라디에이터"
    assert parsed.english_name == "SHROUD"
    assert parsed.price == "17930"


def test_parse_stores_table() -> None:
    html = """
    <table>
      <tr><th>구분</th><th>상호</th><th>주소</th><th>전화번호</th></tr>
      <tr>
        <td>대리점</td>
        <td>(주)형제</td>
        <td>강원도 원주시 봉148</td>
        <td>033-742-2368</td>
      </tr>
      <tr>
        <td>대리점</td>
        <td>강원부품(주)</td>
        <td>강원도 강원특별자치도 원주시 현충로 214</td>
        <td>033-743-185368</td>
      </tr>
    </table>
    """

    parsed = parse_stores(html)

    assert len(parsed) == 2
    assert parsed[0].store_type == "대리점"
    assert parsed[0].name == "(주)형제"
    assert parsed[0].address == "강원도 원주시 봉148"
    assert parsed[0].phone == "033-742-2368"


def test_parse_stores_mobis_list_markup() -> None:
    html = """
    <input type="hidden" id="black" name="black" value="N" />
    <ul class="table-d">
      <li role="row">
        <div><span class="t-td">대리점</span></div>
        <div><span class="t-td">현대</span></div>
        <div><span class="t-td">(주)형제</span></div>
        <div><span class="t-td">강원도 원주시 봉148</span></div>
        <div><span class="t-td">033-742-2368</span></div>
      </li>
    </ul>
    """

    parsed = parse_stores(html)

    assert len(parsed) == 1
    assert parsed[0].store_type == "대리점"
    assert parsed[0].name == "(주)형제"
    assert parsed[0].address == "강원도 원주시 봉148"


def test_black_marker_raises() -> None:
    with pytest.raises(MobisBlockedError):
        parse_price('<input type="hidden" id="black" value="Y" />', "338002F600")


def test_parse_max_page_from_indicator_links() -> None:
    html = """
    <ul class="indicator">
      <li><a onclick="fnSInventoryPageLoad(1); return false;">1</a></li>
      <li><a onclick="fnSInventoryPageLoad(10); return false;">10</a></li>
      <li class="arrow"><a onclick="fnSInventoryPageLoad(15); return false;"></a></li>
    </ul>
    """

    assert parse_max_page(html) == 15
