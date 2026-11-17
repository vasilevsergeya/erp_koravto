"""Replay actual EPC catalog replies recorded on 2026-09-08; no live networking."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from requests import Response

from epc_api.app import create_app
from epc_api.catalog import Catalog, load_profile
from epc_api.errors import PartNotFound
from epc_api.http import EPCSession
from epc_api.service import PartsService
from tests.conftest import FIXTURES, FixtureAdapter

FORM = """<html><script>
Ext.net.ResourceMgr.init({id:"ctl00$cph_main$ResourceManager1",aspForm:"form1"});
</script><form action=""><input type="hidden" name="__VIEWSTATE" value="recorded-form-state">
</form></html>"""


class RecordedEPCAdapter(FixtureAdapter):
    def send(self, request, **kwargs):
        path = urlsplit(request.url).path
        if not path.startswith("/PROGS/"):
            return super().send(request, **kwargs)
        self.calls.append((request, kwargs))
        response = Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        content = FORM
        if request.method == "POST":
            form = parse_qs(request.body, keep_blank_values=True)
            params = json.loads(form["submitDirectEventConfig"][0])["config"]["extraParams"]
            method = form["__EVENTARGUMENT"][0].split("|")[-1]
            number = params.get("SEARCH_VALUE", params.get("P_PARTNO"))
            assert form["__VIEWSTATE"] == ["recorded-form-state"]
            assert request.headers["X-Ext-Net"] == "delta=true"
            if path == "/PROGS/PCHS1020.aspx":
                assert method == "fnSearchClick"
                assert params["P_CARCOD"] == ""  # The real value of the All option.
                assert params["SEARCH_TYPE"] == "PARTNO"
                tag = "search"
            elif method == "fnPageAfterRender":
                tag = "details"
            else:
                assert method == "Get_Applied_Catalog_Section_Info"
                # First search result has 17343A, but the selected history row has 17343K.
                if number == "7701207449":
                    assert params["P_PARTCD"] == "17343K"
                tag = "applied"
            file = FIXTURES / "epc" / f"{tag}-{number}.txt"
            content = file.read_text(encoding="utf-8")
        response._content = content.encode()
        response.encoding = "utf-8"
        return response


def test_full_post_with_bundled_profile_and_real_epc_replies(settings):
    adapter = RecordedEPCAdapter()

    def factory(settings, proxy):
        session = EPCSession(settings, proxy)
        session.session.mount("https://epc.test", adapter)
        return session

    service = PartsService(settings, load_profile(None), factory)
    with TestClient(create_app(settings, service)) as client:
        response = client.post(
            "/parts",
            auth=(settings.api_username, settings.api_password),
            json={"part_numbers": ["1147112920", "7701207449"]},
        )
    assert response.status_code == 200, response.text
    expected = json.loads((FIXTURES / "epc" / "expected-response.json").read_text())
    assert response.json() == expected
    assert len([r for r, _ in adapter.calls if "/PROGS/" in r.url]) == 12


def test_live_default_profile_is_packaged():
    profile = load_profile(None)
    assert profile.search.url.startswith("/PROGS/PCHS1020.aspx")
    assert profile.search.params["P_CARCOD"] == ""


def test_similar_number_in_search_does_not_return_another_part(monkeypatch):
    catalog = Catalog(None, load_profile(None))
    source = (FIXTURES / "epc" / "search-1147112920.txt").read_text(encoding="utf-8")
    calls = []

    def fetch(spec, context):
        calls.append(spec)
        return source

    monkeypatch.setattr(catalog, "fetch", fetch)
    with pytest.raises(PartNotFound):
        catalog.lookup("114711")
    assert len(calls) == 1
