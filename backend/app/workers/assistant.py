"""Separate queue keeps OpenAI/SMTP latency out of inbound processing."""

import asyncio
import contextlib

from arq.connections import RedisSettings

from app.core.config import config
from app.core.logging import configure_logging
from app.services.ai import index_example, process_draft, recovery
from app.services.notifications import notification_loop


async def startup(ctx):
    configure_logging("assistant")
    ctx["ai_recovery"] = asyncio.create_task(recovery(ctx))
    ctx["notification_loop"] = asyncio.create_task(notification_loop())


async def shutdown(ctx):
    for key in ("ai_recovery", "notification_loop"):
        ctx[key].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ctx[key]


class WorkerSettings:
    functions = [process_draft, index_example]
    redis_settings = RedisSettings.from_dsn(config().redis_url)
    queue_name = "arq:ai"
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1
    job_timeout = 60
    keep_result = 0
    health_check_interval = 10
    health_check_key = "crm:assistant:health"
    log_results = False
