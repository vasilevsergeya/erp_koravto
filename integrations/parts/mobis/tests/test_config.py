from pathlib import Path

import pytest

from mobis_api.config import load_settings, normalize_proxy_url


def test_normalize_proxy_url_supports_http_and_socks() -> None:
    assert normalize_proxy_url("user:pass@127.0.0.1:8080") == "http://user:pass@127.0.0.1:8080"
    assert normalize_proxy_url("https://127.0.0.1:8443") == "https://127.0.0.1:8443"
    assert normalize_proxy_url("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert normalize_proxy_url("socks5h://127.0.0.1:1080") == "socks5h://127.0.0.1:1080"


def test_normalize_proxy_url_rejects_unsupported_scheme() -> None:
    with pytest.raises(ValueError, match="unsupported proxy scheme"):
        normalize_proxy_url("socks4://127.0.0.1:1080")


def test_load_settings_supports_structured_proxy_sections(tmp_path: Path) -> None:
    config = tmp_path / "config.ini"
    config.write_text(
        """
        [mobis]
        request_attempts = 5

        [proxy:first]
        scheme = socks5h
        host = 127.0.0.1
        port = 1080
        username = user
        password = pass
        """,
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.request_attempts == 5
    assert settings.proxies == ("socks5h://user:pass@127.0.0.1:1080",)


def test_explicit_deployment_config_must_exist(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setenv("MOBIS_CONFIG", str(tmp_path / "missing.ini"))
    with pytest.raises(ValueError, match="missing"):
        load_settings()


def test_explicit_deployment_config_requires_credentials(monkeypatch, tmp_path):
    import pytest
    config = tmp_path / "config.ini"
    config.write_text("[auth]\nusername=api_user\npassword=change_me\n")
    monkeypatch.setenv("MOBIS_CONFIG", str(config))
    with pytest.raises(ValueError, match="credentials"):
        load_settings()
