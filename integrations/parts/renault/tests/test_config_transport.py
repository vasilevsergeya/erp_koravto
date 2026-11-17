import hashlib
import json
from urllib.parse import parse_qs

import pytest
import requests
from requests import Response

from epc_api.catalog import CatalogProfile, load_profile
from epc_api.config import ConfigurationError, ProxySettings, load_settings
from epc_api.errors import (
    AuthenticationError,
    EPCError,
    ParseError,
    SessionExpired,
    TransportError,
    UpstreamTimeout,
)
from epc_api.http import EPCSession

CONFIG = """[api]
username = api-user
password = test%password
[site]
login_url = https://epc.test/Members/Login.aspx?ReturnUrl=%2f
username = site-user
password = site-password
[proxy]
required = false
"""


def test_ini_percent_and_secret_repr(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(CONFIG, encoding="utf-8")
    settings = load_settings(path)
    assert settings.api_password == "test%password"
    assert settings.login_url.endswith("%2f")
    assert "site-password" not in repr(settings)
    assert "test%password" not in repr(settings)


def test_proxy_required_by_config(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(CONFIG.replace("required = false", "required = true"))
    with pytest.raises(ConfigurationError, match="Прокси обязательны"):
        load_settings(path)


def test_proxy_order_and_credential_encoding(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(
        CONFIG
        + """
[proxy:b]
ip = 192.0.2.2
port = 8080
username = user@name
password = p:a%ss
[proxy:a]
ip = 192.0.2.1
port = 1080
scheme = socks5h
"""
    )
    proxies = load_settings(path).proxies
    assert [p.name for p in proxies] == ["proxy:b", "proxy:a"]
    assert proxies[0].url == "http://user%40name:p%3Aa%25ss@192.0.2.2:8080"
    assert "p:a%ss" not in repr(proxies)


@pytest.mark.parametrize(
    "edit",
    [
        ("required = false", "required = false\n[http]\nread_timeout = nan"),
        ("required = false", "required = false\n[http]\nrequest_interval = -1"),
        ("https://epc.test", "http://epc.test"),
        ("username = site-user", "username = CHANGE_ME_EPC_USER"),
    ],
)
def test_invalid_config_fails_early(tmp_path, edit):
    path = tmp_path / "config.ini"
    path.write_text(CONFIG.replace(*edit))
    with pytest.raises(ConfigurationError):
        load_settings(path)


def test_login_protocol_includes_hash_viewstate_and_language(settings, adapter, session_factory):
    session = session_factory(settings, None)
    session.login()
    assert session.authenticated
    requests_ = [r for r, _ in adapter.calls if r.method == "POST"]
    assert len(requests_) == 2
    language = parse_qs(requests_[1].body)
    assert json.loads(language["submitDirectEventConfig"][0])["config"]["extraParams"] == {
        "_lang": "en"
    }
    login = parse_qs(requests_[0].body)
    params = json.loads(login["submitDirectEventConfig"][0])["config"]["extraParams"]
    assert params["encrypt_passwd"] == hashlib.sha256(b"site-password").hexdigest()
    assert params["passwd"] == "site-password"
    assert login["ctl00$ContentPlaceHolder1$dd_nat"] == ["DOM"]
    assert login["ctl00$ContentPlaceHolder1$cb_save"] == ["on"]
    assert requests_[0].url.lower() == settings.login_url.lower()
    assert requests_[1].url == "https://epc.test/"
    session.close()


def test_login_failure_never_marks_authenticated(settings, adapter, session_factory):
    adapter.login_failure = True
    session = session_factory(settings, None)
    with pytest.raises(AuthenticationError):
        session.login()
    assert not session.authenticated
    session.close()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (requests.Timeout("timeout secret"), UpstreamTimeout),
        (requests.exceptions.ProxyError("http://user:secret@proxy"), TransportError),
    ],
)
def test_transport_redacts_secrets(settings, adapter, session_factory, error, expected):
    adapter.failure = error
    session = session_factory(settings, None)
    with pytest.raises(expected) as exc:
        session.request("GET", "/")
    assert "secret" not in str(exc.value)
    session.close()


def test_off_origin_redirect_rejected_before_second_request(settings, monkeypatch):
    session = EPCSession(settings, None)
    calls = []

    def redirect(method, url, **kwargs):
        calls.append(url)
        response = Response()
        response.status_code = 307
        response.url = url
        response.headers["Location"] = "https://external.test/collect"
        return response

    monkeypatch.setattr(session.session, "request", redirect)
    with pytest.raises(ParseError):
        session.request("POST", "/", data={"password": "secret"})
    assert calls == ["https://epc.test/"]
    session.close()


def test_proxy_sessions_do_not_share_cookies(settings):
    first = EPCSession(settings, ProxySettings("a", "192.0.2.1", 8080))
    second = EPCSession(settings, ProxySettings("b", "192.0.2.2", 8080))
    first.session.cookies.set("auth", "first")
    assert second.session.cookies.get("auth") is None
    assert first.session.trust_env is False
    assert second.session.proxies["https"] == "http://192.0.2.2:8080"
    first.close()
    second.close()


def test_bad_profile_reference_rejected(tmp_path, profile):
    data = profile.model_dump()
    data["result"]["cars"]["response"] = "missing"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError):
        load_profile(path)


def test_direct_method_requires_post(tmp_path, profile):
    data = profile.model_dump()
    data["search"]["direct_method"] = "testMethod"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError):
        load_profile(path)


def test_profile_rejects_unknown_fields(profile):
    data = profile.model_dump() | {"unknown": "value"}
    with pytest.raises(ValueError):
        CatalogProfile.model_validate(data)


@pytest.mark.parametrize(
    "script",
    [
        "window.location.href='/Members/Login.aspx';",
        "Ext.net.ResourceMgr.redirect('/Members/Login.aspx?ReturnUrl=%2F');",
    ],
)
def test_javascript_login_redirect_is_expired_session(settings, script):
    session = EPCSession(settings, None)
    session.authenticated = True
    response = Response()
    response.url = "https://epc.test/catalog"
    response._content = json.dumps({"script": script}).encode()
    with pytest.raises(SessionExpired):
        session.check_protected_response(response)
    assert not session.authenticated
    session.close()


def test_failed_envelope_is_not_empty_search(settings):
    session = EPCSession(settings, None)
    response = Response()
    response.url = "https://epc.test/catalog"
    response._content = b'{"success":false,"rows":[]}'
    with pytest.raises(EPCError):
        session.check_protected_response(response)
    session.close()
