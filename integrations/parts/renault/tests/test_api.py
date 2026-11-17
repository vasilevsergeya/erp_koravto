from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from epc_api.app import create_app
from epc_api.config import ProxySettings
from epc_api.service import PartsService

AUTH = ("api-user", "api-password")


def test_post_end_to_end(settings, profile, adapter, session_factory):
    service = PartsService(settings, profile, session_factory)
    with TestClient(create_app(settings, service)) as client:
        response = client.post(
            "/parts", auth=AUTH, json={"part_numbers": ["1147112920", "7701207449", "1147112920"]}
        )
    assert response.status_code == 200, response.text
    first, second, duplicate = response.json()["items"]
    assert first == duplicate
    assert first == {
        "part_number": "1147112920",
        "english_name": "GASKET-OIL PUMP,CYLINDER BLOCK",
        "weight": 1,
        "volume": 0.4,
        "compatibility": [
            {"part_number": "1147112900", "adopted": "1998-01-01"},
            {"part_number": "1147112910", "adopted": "2001-10-01"},
        ],
        "price": 1100,
        "stock": 688,
        "cars": ["SM5", "NewSM5/SM7"],
    }
    assert second["compatibility"] == []
    assert second["cars"] == ["New SM5(L43)", "All-New SM7", "SM6", "SM6"]
    searches = [request for request, _ in adapter.calls if "/fixture/search?" in request.url]
    assert len(searches) == 2
    assert parse_qs(urlsplit(searches[0].url).query)["model"] == ["All"]


@pytest.mark.parametrize("auth", [None, ("api-user", "wrong"), ("wrong", "api-password")])
@pytest.mark.parametrize("path", ["/parts", "/docs", "/openapi.json", "/health/ready"])
def test_requires_basic(settings, profile, adapter, session_factory, auth, path):
    with TestClient(
        create_app(settings, PartsService(settings, profile, session_factory))
    ) as client:
        response = (
            client.post(path, auth=auth, json={"part_numbers": ["1147112920"]})
            if path == "/parts"
            else client.get(path, auth=auth)
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert adapter.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"part_numbers": []},
        {"part_numbers": [123]},
        {"part_numbers": [None]},
        {"part_numbers": ["../login"]},
        {"part_numbers": ["<script>"]},
        {"part_numbers": [" "]},
        {"part_numbers": ["1147112920"], "extra": 1},
        {"part_numbers": ["1"] * 51},
    ],
)
def test_validation_before_network(settings, profile, adapter, session_factory, body):
    with TestClient(
        create_app(settings, PartsService(settings, profile, session_factory))
    ) as client:
        response = client.post("/parts", auth=AUTH, json=body)
    assert response.status_code == 422
    assert adapter.calls == []


def test_missing_detail_is_explicit_404(settings, profile, session_factory):
    with TestClient(
        create_app(settings, PartsService(settings, profile, session_factory))
    ) as client:
        response = client.post("/parts", auth=AUTH, json={"part_numbers": ["0000000000"]})
    assert response.status_code == 404
    assert response.json()["error"]["part_number"] == "0000000000"


def test_auth_failure_is_upstream_error(settings, profile, adapter, session_factory):
    adapter.login_failure = True
    with TestClient(
        create_app(settings, PartsService(settings, profile, session_factory))
    ) as client:
        response = client.post("/parts", auth=AUTH, json={"part_numbers": ["1147112920"]})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_authentication_failed"
    assert "site-password" not in response.text


def test_no_profile_is_honest_503(settings, adapter, session_factory):
    with TestClient(create_app(settings, PartsService(settings, None, session_factory))) as client:
        response = client.post("/parts", auth=AUTH, json={"part_numbers": ["1147112920"]})
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready", auth=AUTH).status_code == 503
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "catalog_not_configured"
    assert adapter.calls == []


def test_proxy_rotates_per_api_request(settings, profile, adapter, session_factory):
    proxies = (
        ProxySettings("first", "192.0.2.1", 8080),
        ProxySettings("second", "192.0.2.2", 8080),
    )
    settings = replace(settings, proxies=proxies, require_proxy=True)
    with TestClient(
        create_app(settings, PartsService(settings, profile, session_factory))
    ) as client:
        for expected in (proxies[0], proxies[1], proxies[0]):
            before = len(adapter.calls)
            response = client.post("/parts", auth=AUTH, json={"part_numbers": ["1147112920"]})
            assert response.status_code == 200
            assert all(
                call[1]["proxies"]["https"] == expected.url for call in adapter.calls[before:]
            )


def test_expired_session_reauthenticates_once(settings, profile, adapter, session_factory):
    adapter.expire_once = True
    with TestClient(
        create_app(settings, PartsService(settings, profile, session_factory))
    ) as client:
        response = client.post("/parts", auth=AUTH, json={"part_numbers": ["1147112920"]})
    assert response.status_code == 200
    logins = [r for r, _ in adapter.calls if r.body and "callLoginProcess" in r.body]
    assert len(logins) == 2


def test_every_api_call_reads_fresh_data(settings, profile, adapter, session_factory):
    with TestClient(
        create_app(settings, PartsService(settings, profile, session_factory))
    ) as client:
        for _ in range(2):
            assert (
                client.post("/parts", auth=AUTH, json={"part_numbers": ["1147112920"]}).status_code
                == 200
            )
    assert sum("/fixture/search?" in r.url for r, _ in adapter.calls) == 2


def test_openapi_uses_external_prefix(settings, profile, adapter, session_factory, monkeypatch):
    monkeypatch.setenv("EPC_PUBLIC_PREFIX", "/api/renault")
    with TestClient(create_app(settings, PartsService(settings, profile, session_factory))) as client:
        response = client.get("/openapi.json", auth=AUTH)
    assert response.status_code == 200
    assert response.json()["servers"] == [{"url": "/api/renault"}]
    assert "/parts" in response.json()["paths"]
