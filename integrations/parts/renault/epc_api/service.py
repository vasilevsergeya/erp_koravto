import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

from epc_api.catalog import Catalog, CatalogProfile
from epc_api.config import ConfigurationError, Settings
from epc_api.errors import CatalogNotConfigured, EPCError, ServiceBusy, SessionExpired
from epc_api.http import EPCSession
from epc_api.models import PartsResponse


@dataclass
class Slot:
    session: EPCSession
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionPool:
    def __init__(self, settings: Settings, session_factory=EPCSession):
        if settings.require_proxy and not settings.proxies:
            raise ConfigurationError("Прокси обязательны, но не настроены.")
        self.slots = [
            Slot(session_factory(settings, proxy)) for proxy in settings.proxies or (None,)
        ]
        self.cursor = 0
        self.lock = threading.Lock()
        self.queue_timeout = settings.queue_timeout

    @contextmanager
    def acquire(self):
        with self.lock:
            slot = self.slots[self.cursor]
            self.cursor = (self.cursor + 1) % len(self.slots)
        if not slot.lock.acquire(timeout=self.queue_timeout):
            raise ServiceBusy()
        try:
            yield slot.session
        finally:
            slot.lock.release()

    def close(self):
        for slot in self.slots:
            slot.session.close()


class PartsService:
    def __init__(
        self,
        settings: Settings,
        profile: CatalogProfile | None,
        session_factory=EPCSession,
        catalog_factory=Catalog,
    ):
        self.pool = SessionPool(settings, session_factory)
        self.profile = profile
        self.catalog_factory = catalog_factory

    @property
    def configured(self):
        return self.profile is not None

    def lookup(self, part_numbers: list[str]) -> PartsResponse:
        if not self.configured:
            raise CatalogNotConfigured()
        with self.pool.acquire() as session:
            cache = {}
            for part_number in part_numbers:
                if part_number in cache:
                    continue
                try:
                    if not session.authenticated:
                        session.login()
                    catalog = self.catalog_factory(session, self.profile)
                    try:
                        cache[part_number] = catalog.lookup(part_number)
                    except SessionExpired:
                        # Exactly one reauthentication, on the same proxy, then retry the read.
                        session.authenticated = False
                        session.login()
                        cache[part_number] = catalog.lookup(part_number)
                except EPCError as exc:
                    if isinstance(exc, SessionExpired):
                        session.authenticated = False
                    exc.part_number = part_number
                    raise
            return PartsResponse(items=[cache[number] for number in part_numbers])

    def close(self):
        self.pool.close()
