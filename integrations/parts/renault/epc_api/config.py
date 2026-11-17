from configparser import ConfigParser
from configparser import Error as IniError
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ProxySettings:
    name: str
    host: str
    port: int
    scheme: str = "http"
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)

    @property
    def url(self) -> str:
        credentials = ""
        if self.username:
            credentials = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{credentials}{host}:{self.port}"


@dataclass(frozen=True)
class Settings:
    api_username: str = field(repr=False)
    api_password: str = field(repr=False)
    login_url: str
    site_username: str = field(repr=False)
    site_password: str = field(repr=False)
    site_domain: str = "DOM"
    host: str = "127.0.0.1"
    port: int = 8000
    connect_timeout: float = 10
    read_timeout: float = 30
    queue_timeout: float = 30
    request_interval: float = 0.3
    max_batch_size: int = 50
    proxies: tuple[ProxySettings, ...] = ()
    require_proxy: bool = True
    catalog_path: Path | None = None


def load_settings(path: str | Path) -> Settings:
    path = Path(path).resolve()
    ini = ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8-sig") as handle:
            ini.read_file(handle)
        for section in ("api", "site", "proxy"):
            if not ini.has_section(section):
                raise ConfigurationError(f"Отсутствует секция [{section}].")

        def required(section: str, key: str) -> str:
            value = ini.get(section, key, fallback="").strip()
            if not value or value.startswith("CHANGE_ME"):
                raise ConfigurationError(f"Заполните [{section}] {key} в {path.name}.")
            return value

        login_url = required("site", "login_url")
        parsed = urlsplit(login_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.fragment:
            raise ConfigurationError("[site] login_url должен быть HTTPS URL без учётных данных.")
        domain = ini.get("site", "domain", fallback="DOM").upper()
        if domain not in {"DOM", "EXP", "OEM"}:
            raise ConfigurationError("[site] domain: допустимы DOM, EXP, OEM.")
        proxies = []
        for section in ini.sections():
            if not section.startswith("proxy:") or not ini.getboolean(
                section, "enabled", fallback=True
            ):
                continue
            host = required(section, "ip").strip("[]")
            if any(char.isspace() or char in "/@?#\\" for char in host):
                raise ConfigurationError(f"[{section}] ip должен содержать только IP или hostname.")
            port = ini.getint(section, "port")
            scheme = ini.get(section, "scheme", fallback="http").lower()
            if not 1 <= port <= 65535 or scheme not in {"http", "https", "socks5h"}:
                raise ConfigurationError(f"Некорректные port/scheme в [{section}].")
            username = ini.get(section, "username", fallback="")
            password = ini.get(section, "password", fallback="")
            if password and not username:
                raise ConfigurationError(f"В [{section}] указан пароль без username.")
            proxies.append(ProxySettings(section, host, port, scheme, username, password))
        require_proxy = ini.getboolean("proxy", "required", fallback=True)
        if require_proxy and not proxies:
            raise ConfigurationError(
                "Прокси обязательны: заполните хотя бы одну секцию [proxy:имя]."
            )
        catalog = ini.get("site", "catalog_profile", fallback="").strip()
        settings = Settings(
            api_username=required("api", "username"),
            api_password=required("api", "password"),
            login_url=login_url,
            site_username=required("site", "username"),
            site_password=required("site", "password"),
            site_domain=domain,
            host=ini.get("api", "host", fallback="127.0.0.1"),
            port=ini.getint("api", "port", fallback=8000),
            connect_timeout=ini.getfloat("http", "connect_timeout", fallback=10),
            read_timeout=ini.getfloat("http", "read_timeout", fallback=30),
            queue_timeout=ini.getfloat("http", "queue_timeout", fallback=30),
            request_interval=ini.getfloat("http", "request_interval", fallback=0.3),
            max_batch_size=ini.getint("api", "max_batch_size", fallback=50),
            proxies=tuple(proxies),
            require_proxy=require_proxy,
            catalog_path=(path.parent / catalog).resolve() if catalog else None,
        )
        if not 1 <= settings.port <= 65535 or not 1 <= settings.max_batch_size <= 100:
            raise ConfigurationError("Некорректный API port или max_batch_size (1–100).")
        if (
            any(
                not 0 < value <= 300
                for value in (
                    settings.connect_timeout,
                    settings.read_timeout,
                    settings.queue_timeout,
                )
            )
            or not 0 <= settings.request_interval <= 60
        ):
            raise ConfigurationError(
                "Таймауты должны быть в диапазоне (0, 300], интервал — [0, 60]."
            )
        return settings
    except (IniError, OSError, ValueError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError(f"Не удалось прочитать настройки {path.name}.") from None
