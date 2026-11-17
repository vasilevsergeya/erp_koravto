import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from requests import Response
from requests.adapters import BaseAdapter

from epc_api.catalog import load_profile
from epc_api.config import Settings
from epc_api.http import EPCSession

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN = """<html><script>
Ext.net.ResourceMgr.init({id:"ctl00$ContentPlaceHolder1$ResourceManager1",aspForm:"credentials"});
</script><form action="/Members/Login.aspx?ReturnUrl=%2f">
<input type="hidden" name="__VIEWSTATE" value="test-viewstate">
<input type="hidden" name="__EVENTVALIDATION" value="test-validation">
<input id="cred_userid_inputtext"><input id="cred_password_inputtext">
</form></html>"""

HOME = """<html><script>
Ext.net.ResourceMgr.init({id:"ctl00$cph_main$ResourceManager1",aspForm:"form1"});
</script><form action="/">
<input type="hidden" name="__VIEWSTATE" value="test-viewstate">
</form></html>"""


@pytest.fixture
def settings():
    return Settings(
        api_username="api-user",
        api_password="api-password",
        login_url="https://epc.test/Members/Login.aspx?ReturnUrl=%2f",
        site_username="site-user",
        site_password="site-password",
        require_proxy=False,
        request_interval=0,
    )


@pytest.fixture
def profile():
    return load_profile(FIXTURES / "profile.json")


class FixtureAdapter(BaseAdapter):
    """A synthetic site behind a real requests.Session, never the production EPC."""

    def __init__(self):
        self.calls = []
        self.login_failure = False
        self.authenticated = False
        self.expire_once = False
        self.failure = None

    def send(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.failure:
            raise self.failure
        path = urlsplit(request.url).path
        response = Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        content = "{}"
        if path == "/Members/Login.aspx":
            if request.method == "GET":
                content = LOGIN
            else:
                form = parse_qs(request.body)
                assert form["__VIEWSTATE"] == ["test-viewstate"]
                assert request.headers["X-Ext-Net"] == "delta=true"
                method = form["__EVENTARGUMENT"][0].split("|")[-1]
                if method == "callLoginProcess":
                    self.authenticated = not self.login_failure
                    content = (
                        "{script:\"Ext.Msg.show({title: 'Login Failure'});\"}"
                        if self.login_failure
                        else "{script:\"window.location='/';\"}"
                    )
        elif path == "/":
            content = (HOME if self.authenticated else LOGIN) if request.method == "GET" else "{}"
        elif self.expire_once:
            self.expire_once = False
            self.authenticated = False
            content = LOGIN
        elif path == "/fixture/search":
            number = parse_qs(urlsplit(request.url).query)["number"][0]
            content = json.dumps(
                {
                    "rows": [{"part_number": number}]
                    if (FIXTURES / f"{number}.json").exists()
                    else []
                }
            )
        elif path.startswith("/fixture/details/"):
            content = (FIXTURES / f"{path.split('/')[-1]}.json").read_text()
        else:
            response.status_code = 404
        response._content = content.encode()
        response.encoding = "utf-8"
        return response

    def close(self):
        pass


@pytest.fixture
def adapter():
    return FixtureAdapter()


@pytest.fixture
def session_factory(adapter):
    def create(settings, proxy):
        session = EPCSession(settings, proxy)
        session.session.mount("https://epc.test", adapter)
        return session

    return create
