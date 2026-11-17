class EPCError(Exception):
    status_code = 502
    code = "upstream_error"
    message = "Ошибка ответа каталога EPC."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


class AuthenticationError(EPCError):
    code = "upstream_authentication_failed"
    message = "EPC отклонил авторизацию. Проверьте данные [site] в INI-файле."


class SessionExpired(EPCError):
    code = "upstream_session_expired"
    message = "Сессия EPC истекла."


class UpstreamTimeout(EPCError):
    status_code = 504
    code = "upstream_timeout"
    message = "Каталог EPC или прокси не ответил вовремя."


class TransportError(EPCError):
    code = "upstream_connection_failed"
    message = "Не удалось подключиться к EPC через выбранное соединение."


class ParseError(EPCError):
    code = "upstream_format_changed"
    message = "Ответ EPC не соответствует ожидаемой структуре."


class PartNotFound(EPCError):
    status_code = 404
    code = "part_not_found"
    message = "Деталь не найдена в каталоге EPC."


class CatalogNotConfigured(EPCError):
    status_code = 503
    code = "catalog_not_configured"
    message = "Не загружен профиль каталога EPC."


class ServiceBusy(EPCError):
    status_code = 503
    code = "service_busy"
    message = "Все соединения заняты. Повторите запрос позже."
