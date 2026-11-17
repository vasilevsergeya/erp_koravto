import json

import pytest

from epc_api.errors import ParseError
from epc_api.parsing import adopted_date, build_part, extract_rows, number
from tests.conftest import FIXTURES


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (".4", 0.4),
        ("1,100", 1100),
        ("28,600", 28600),
        ("0", 0),
        ("", None),
        (None, None),
    ],
)
def test_numeric_formats(value, expected):
    assert number(value) == expected


@pytest.mark.parametrize("value", ["NaN", "inf", "-10", "1,1", "123xyz", "1 100", "1.2.3"])
def test_bad_number_is_not_zero(value):
    with pytest.raises(ParseError):
        number(value)


def test_fractional_stock_rejected():
    with pytest.raises(ParseError):
        number("1.5", integer=True)


def test_date_not_hardcoded():
    assert str(adopted_date("2001.10.01")) == "2001-10-01"
    with pytest.raises(ParseError):
        adopted_date("2001-13-01")


def test_stock_sum_and_unknown(settings):
    data = json.loads((FIXTURES / "1147112920.json").read_text())
    data["stock"] = [{"stock": "2"}, {"stock": "3"}]
    assert build_part("1147112920", **data).stock == 5
    data["stock"][1]["stock"] = ""
    assert build_part("1147112920", **data).stock is None


def test_wrong_part_and_missing_columns_are_not_success():
    data = json.loads((FIXTURES / "1147112920.json").read_text())
    with pytest.raises(ParseError):
        build_part("7701207449", **data)
    del data["general"][0]["weight"]
    with pytest.raises(ParseError):
        build_part("1147112920", **data)


def test_html_table_and_entities():
    source = (
        '<table id="parts"><tr><th>Part No</th><th>Part Nm</th></tr>'
        "<tr><td>001234</td><td>KIT &amp; SEAL</td></tr></table>"
    )
    assert extract_rows(source, "html", "#parts") == [
        {"Part No": "001234", "Part Nm": "KIT & SEAL"}
    ]
    with pytest.raises(ParseError):
        extract_rows(source, "html", "#missing")


def test_extnet_data_without_javascript_execution():
    source = json.dumps({"script": "App.History.loadData([{part: '001', name: 'A ] bracket'}]);"})
    assert extract_rows(source, "ext_store", "App.History") == [
        {"part": "001", "name": "A ] bracket"}
    ]
    malicious = json.dumps({"script": "App.History.loadData([evil()]);"})
    with pytest.raises(ParseError):
        extract_rows(malicious, "ext_store", "App.History")


def test_missing_store_does_not_mean_empty_results():
    with pytest.raises(ParseError):
        extract_rows('{script:""}', "ext_store", "App.History")
    assert extract_rows('{script:"App.History.loadData([]);"}', "ext_store", "App.History") == []


@pytest.mark.parametrize("script", [None, 123, [], {}])
def test_invalid_extnet_script_is_a_format_error(script):
    with pytest.raises(ParseError):
        extract_rows(json.dumps({"script": script}), "ext_store", "App.History")
