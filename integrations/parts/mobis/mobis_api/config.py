from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit


SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    auth_username: str
    auth_password: str
    mobis_base_url: str
    timeout_seconds: float
    request_attempts: int
    max_store_pages: int
    proxies: tuple[str, ...]
    request_deadline_seconds: float = 45.0
    proxy_min_interval_seconds: float = 0.5
    proxy_cooldown_seconds: float = 2.0
    proxy_slow_seconds: float = 8.0
    session_ttl_seconds: float = 900.0
    store_page_concurrency: int = 3


def normalize_proxy_url(proxy: str, default_scheme: str = "http") -> str:
    value = proxy.strip()
    if not value:
        raise ValueError("proxy URL cannot be empty")

    if "://" not in value:
        value = f"{default_scheme}://{value}"

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        supported = ", ".join(sorted(SUPPORTED_PROXY_SCHEMES))
        raise ValueError(f"unsupported proxy scheme '{parsed.scheme}'. Supported schemes: {supported}")
    if not parsed.hostname or not parsed.port:
        raise ValueError("proxy URL must include host and port")

    return value


def _proxy_from_section(section: configparser.SectionProxy, default_scheme: str) -> str:
    scheme = section.get("scheme", "http").strip()
    host = section.get("host", section.get("ip", "")).strip()
    port = section.get("port", "").strip()
    username = section.get("username", section.get("name", "")).strip()
    password = section.get("password", "").strip()

    if not host or not port:
        raise ValueError("proxy sections must define host/ip and port")

    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        auth += "@"

    return normalize_proxy_url(f"{scheme}://{auth}{host}:{port}", default_scheme=default_scheme)


def _load_proxies(config: configparser.ConfigParser) -> tuple[str, ...]:
    proxies: list[str] = []
    default_scheme = config.get("mobis", "proxy_default_scheme", fallback="http").strip()

    if config.has_section("proxies"):
        for _, value in config.items("proxies"):
            proxy = value.strip()
            if proxy:
                proxies.append(normalize_proxy_url(proxy, default_scheme=default_scheme))

    for section_name in config.sections():
        if section_name.startswith("proxy:"):
            proxies.append(_proxy_from_section(config[section_name], default_scheme=default_scheme))

    return tuple(proxies)


def load_settings(config_path: str | os.PathLike[str] | None = None) -> Settings:
    path = Path(config_path or os.getenv("MOBIS_CONFIG", "config.ini"))
    if os.getenv("MOBIS_CONFIG") and not path.is_file():
        raise ValueError("MOBIS_CONFIG file is missing")
    config = configparser.ConfigParser()
    config.read(path, encoding="utf-8")
    if os.getenv("MOBIS_CONFIG") and any(
        not config.get("auth", key, fallback="").strip()
        or config.get("auth", key, fallback="").startswith("CHANGE_ME")
        or config.get("auth", key, fallback="") == "change_me"
        for key in ("username", "password")
    ):
        raise ValueError("Mobis API credentials must be configured")
    proxies = _load_proxies(config)
    request_attempts = config.getint("mobis", "request_attempts", fallback=max(1, len(proxies)))

    return Settings(
        host=config.get("app", "host", fallback="0.0.0.0"),
        port=config.getint("app", "port", fallback=8000),
        auth_username=config.get("auth", "username", fallback="api_user"),
        auth_password=config.get("auth", "password", fallback="change_me"),
        mobis_base_url=config.get("mobis", "base_url", fallback="https://www.mobis-as.com").rstrip("/"),
        timeout_seconds=config.getfloat("mobis", "timeout_seconds", fallback=30.0),
        request_attempts=max(1, request_attempts),
        max_store_pages=config.getint("mobis", "max_store_pages", fallback=100),
        proxies=proxies,
        request_deadline_seconds=max(1.0, config.getfloat("mobis", "request_deadline_seconds", fallback=45.0)),
        proxy_min_interval_seconds=max(0.0, config.getfloat("mobis", "proxy_min_interval_seconds", fallback=0.5)),
        proxy_cooldown_seconds=max(0.0, config.getfloat("mobis", "proxy_cooldown_seconds", fallback=2.0)),
        proxy_slow_seconds=max(0.0, config.getfloat("mobis", "proxy_slow_seconds", fallback=8.0)),
        session_ttl_seconds=max(1.0, config.getfloat("mobis", "session_ttl_seconds", fallback=900.0)),
        store_page_concurrency=max(1, config.getint("mobis", "store_page_concurrency", fallback=3)),
    )
