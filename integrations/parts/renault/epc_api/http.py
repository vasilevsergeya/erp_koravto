"""HTTP and the Ext.NET login protocol observed on the live login page."""

import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlsplit

import json5
import requests
from bs4 import BeautifulSoup

from epc_api.config import ProxySettings, Settings
from epc_api.errors import (
    AuthenticationError,
    EPCError,
    ParseError,
    SessionExpired,
    TransportError,
    UpstreamTimeout,
)


def is_login_page(response: requests.Response) -> bool:
    return "cred_userid_inputtext" in response.text and "cred_password_inputtext" in response.text


def form_state(html: str) -> tuple[dict[str, str], str, str]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    manager = re.search(r'Ext\.net\.ResourceMgr\.init\(\{id:"([^"]+)"', html)
    if form is None or manager is None:
        raise ParseError("В странице EPC не найдена форма Ext.NET или ResourceManager.")
    data = {tag["name"]: tag.get("value", "") for tag in form.select('input[type="hidden"][name]')}
    return data, manager.group(1), str(form.get("action", ""))


def ext_payload(html: str, method: str, params: dict) -> tuple[dict, str]:
    data, manager, action = form_state(html)
    data.update(
        {
            "__EVENTTARGET": manager,
            "__EVENTARGUMENT": f"-|public|{method}",
            "submitDirectEventConfig": json.dumps({"config": {"extraParams": params}}),
        }
    )
    return data, action


class EPCSession:
    """Used exclusively under a pool-slot lock; cookies never cross proxies."""

    def __init__(self, settings: Settings, proxy: ProxySettings | None):
        self.settings = settings
        self.session = requests.Session()
        # Do not accidentally bypass the configured proxy via ambient NO_PROXY.
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "RenaultKoreaEPC-API/0.1 (Python requests)"})
        if proxy:
            self.session.proxies = {"http": proxy.url, "https": proxy.url}
        self.authenticated = False
        self.last_request = 0.0

    def safe_url(self, value: str) -> str:
        url = urljoin(self.settings.login_url, value)
        target, origin = urlsplit(url), urlsplit(self.settings.login_url)
        # Expired EPC sessions redirect to the same login page over HTTP.
        # Never follow that downgrade: trigger the existing HTTPS re-login once.
        if (
            self.authenticated
            and target.scheme == "http"
            and target.hostname == origin.hostname
            and (target.port or 80) == 80
            and target.username is None
            and target.password is None
            and target.path.lower() == origin.path.lower()
        ):
            self.authenticated = False
            raise SessionExpired()
        if (
            target.scheme != "https"
            or target.hostname != origin.hostname
            or (target.port or 443) != (origin.port or 443)
            or target.username is not None
            or target.password is not None
        ):
            raise ParseError("EPC вернул переход за пределы настроенного HTTPS-сайта.")
        return url

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        url = self.safe_url(url)
        # Handle redirects ourselves so session cookies and POST secrets stay on-origin.
        for _ in range(6):
            delay = self.settings.request_interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=(self.settings.connect_timeout, self.settings.read_timeout),
                    allow_redirects=False,
                    **kwargs,
                )
            except requests.Timeout:
                raise UpstreamTimeout() from None
            except requests.RequestException:
                # requests exceptions may contain proxy credentials; never expose them.
                raise TransportError() from None
            finally:
                self.last_request = time.monotonic()
            if response.is_redirect:
                url = self.safe_url(urljoin(response.url, response.headers["Location"]))
                kwargs.pop("params", None)
                if response.status_code in {301, 302, 303}:
                    method = "GET"
                    kwargs.pop("data", None)
                    kwargs.pop("json", None)
                continue
            if response.status_code in {401, 403}:
                raise SessionExpired()
            if response.status_code >= 400:
                raise EPCError(f"EPC вернул HTTP {response.status_code}.")
            return response
        raise TransportError("Слишком много перенаправлений при обращении к EPC.")

    def direct(self, page: requests.Response, method: str, params: dict, fields=None):
        data, action = ext_payload(page.text, method, params)
        data.update(fields or {})
        response = self.request(
            "POST",
            urljoin(page.url, action),
            data=data,
            headers={
                "X-Ext-Net": "delta=true",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": f"https://{urlsplit(page.url).netloc}",
                "Referer": page.url,
            },
        )
        return response

    def login(self) -> None:
        self.authenticated = False
        try:
            page = self.request("GET", self.settings.login_url)
            password_hash = hashlib.sha256(self.settings.site_password.encode()).hexdigest()
            result = self.direct(
                page,
                "callLoginProcess",
                {
                    "_user_id": self.settings.site_username,
                    "passwd": self.settings.site_password,
                    "encrypt_passwd": password_hash,
                },
                fields={
                    "ctl00$ContentPlaceHolder1$dd_nat": self.settings.site_domain,
                    "ctl00$ContentPlaceHolder1$cred_userid_inputtext": self.settings.site_username,
                    "ctl00$ContentPlaceHolder1$cred_password_inputtext": (
                        self.settings.site_password
                    ),
                    "encrypt_passwd": password_hash,
                    "ctl00$ContentPlaceHolder1$cb_save": "on",
                },
            )
            try:
                result_data = json5.loads(result.text)
            except ValueError:
                raise AuthenticationError() from None
            if not isinstance(result_data, dict) or result_data.get("success") is False:
                raise AuthenticationError()
            script = str(result_data.get("script", ""))
            if "Login Failure" in script or "Ext.Msg.ERROR" in script or "win_otp" in script:
                raise AuthenticationError()
            # Do not execute server-returned JS. Verify actual access to a protected page.
            home = self.request("GET", "/")
            if (
                is_login_page(home)
                or urlsplit(home.url).path.lower() == urlsplit(self.settings.login_url).path.lower()
            ):
                raise AuthenticationError()
            # Login may restore the account's language cookie. Set English afterwards.
            language = self.direct(home, "fnLANGChange", {"_lang": "en"})
            try:
                language_result = json5.loads(language.text)
            except ValueError:
                raise ParseError("Не удалось прочитать ответ выбора языка EPC.") from None
            if not isinstance(language_result, dict) or language_result.get("success") is False:
                raise ParseError("EPC не подтвердил выбор английского языка.")
            self.authenticated = True
        except SessionExpired:
            raise AuthenticationError() from None

    def protected_request(self, method: str, url: str, **kwargs) -> requests.Response:
        response = self.request(method, url, **kwargs)
        return self.check_protected_response(response)

    def check_protected_response(self, response: requests.Response) -> requests.Response:
        if (
            is_login_page(response)
            or urlsplit(response.url).path.lower() == urlsplit(self.settings.login_url).path.lower()
        ):
            self.authenticated = False
            raise SessionExpired()
        # Ext.NET can expire a session with a JavaScript redirect inside HTTP 200.
        try:
            envelope = json5.loads(response.text)
        except ValueError:
            envelope = None
        if isinstance(envelope, dict):
            script = str(envelope.get("script", ""))
            redirects = re.finditer(
                r"(?:location(?:\.href)?\s*=|(?:location\.(?:assign|replace)|"
                r"Ext\.net\.ResourceMgr\.redirect)\s*\()\s*[\"']([^\"']+)",
                script,
            )
            for redirect in redirects:
                url = self.safe_url(urljoin(response.url, redirect.group(1)))
                if urlsplit(url).path.lower() == urlsplit(self.settings.login_url).path.lower():
                    self.authenticated = False
                    raise SessionExpired()
            if envelope.get("success") is False:
                raise EPCError("EPC сообщил об ошибке выполнения запроса.")
        return response

    def close(self) -> None:
        self.session.close()
