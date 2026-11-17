import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from epc_api.config import ProxySettings
from epc_api.errors import ServiceBusy
from epc_api.service import SessionPool


def test_concurrent_rotation_is_thread_safe(settings):
    settings = replace(
        settings, proxies=tuple(ProxySettings(str(i), f"192.0.2.{i}", 8080) for i in range(1, 4))
    )
    pool = SessionPool(settings)
    active = set()
    lock = threading.Lock()

    def work(_):
        with pool.acquire() as session:
            with lock:
                assert id(session) not in active
                active.add(id(session))
            proxy = session.session.proxies["https"]
            with lock:
                active.remove(id(session))
            return proxy

    with ThreadPoolExecutor(max_workers=8) as executor:
        result = list(executor.map(work, range(60)))
    for proxy in settings.proxies:
        assert result.count(proxy.url) == 20
    pool.close()


def test_busy_slot_has_bounded_wait_and_releases_after_error(settings):
    pool = SessionPool(replace(settings, queue_timeout=0.01))
    with pool.acquire():
        with pytest.raises(ServiceBusy), pool.acquire():
            pass
    with pytest.raises(RuntimeError), pool.acquire():
        raise RuntimeError("test")
    with pool.acquire() as session:
        assert session is not None
    pool.close()
