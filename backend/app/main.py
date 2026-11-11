import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import cast

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.responses import JSONResponse

from app.api import auth, inbox, settings, webhooks
from app.core.config import config
from app.core.db import SessionLocal, engine
from app.core.logging import configure_logging
from app.core.security import token_hash
from app.core.settings import redis_client
from app.models import LoginSession, User, now

log = logging.getLogger("crm")


@asynccontextmanager
async def lifespan(app):
    configure_logging("api")
    yield
    engine.dispose()


app = FastAPI(
    title="KORAVTO CRM",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
for router in (auth.router, settings.router, webhooks.router, inbox.router):
    app.include_router(router)

_buckets: OrderedDict = OrderedDict()


@app.middleware("http")
async def protect_and_log(request: Request, call_next):
    correlation = str(uuid.uuid4())
    started = time.monotonic()
    status = 500
    is_webhook = request.url.path.startswith("/api/webhooks/")
    try:
        if request.method not in ("GET", "HEAD", "OPTIONS") and not is_webhook:
            if request.headers.get("origin", "") != config().public_url:
                status = 403
                return JSONResponse(
                    {"detail": "Недопустимый источник запроса"},
                    status_code=status,
                    headers={"X-Request-ID": correlation, "Cache-Control": "no-store"},
                )
        if is_webhook:
            ip = request.client.host if request.client else "unknown"
            tick = int(time.monotonic()) // 60
            previous_tick, count = _buckets.get(ip, (tick, 0))
            count = count + 1 if previous_tick == tick else 1
            _buckets[ip] = (tick, count)
            _buckets.move_to_end(ip)
            while len(_buckets) > 10000:
                _buckets.popitem(last=False)
            if count > 1800:
                status = 429
                return JSONResponse(
                    {"detail": "Rate limit"},
                    status_code=status,
                    headers={"X-Request-ID": correlation, "Cache-Control": "no-store"},
                )
        response = await call_next(request)
        status = response.status_code
    except Exception as error:
        log.error(
            "request.failed",
            extra={"correlation_id": correlation, "error_type": type(error).__name__},
        )
        response = JSONResponse(
            {"detail": "Внутренняя ошибка", "correlation_id": correlation}, status_code=500
        )
    finally:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        log.info(
            "http.request",
            extra={
                "correlation_id": correlation,
                "route": route,
                "method": request.method,
                "status": status,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
    response.headers["X-Request-ID"] = correlation
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_input(request, error):
    # Pydantic input values can contain passwords: never include them in the response.
    return JSONResponse({"detail": "Проверьте заполнение полей"}, status_code=422)


@app.get("/health", include_in_schema=False)
def health():
    result = {"database": "down", "redis": "down", "worker": "down"}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception:
        pass
    try:
        redis_client.ping()
        result["redis"] = "ok"
        heartbeat = cast(bytes | None, redis_client.get("crm:worker:heartbeat"))
        if heartbeat and time.time() - float(heartbeat) < 45:
            result["worker"] = "ok"
    except Exception:
        pass
    ok = all(value == "ok" for value in result.values())
    return JSONResponse({"ok": ok, "services": result}, status_code=200 if ok else 503)


@app.get("/api/openapi.json", include_in_schema=False)
def schema(user=Depends(auth.admin_user)):
    return app.openapi()


@app.get("/api/docs", include_in_schema=False)
def docs(user=Depends(auth.admin_user)):
    # A self-contained API reference; no public docs endpoint or third-party scripts.
    from html import escape

    from fastapi.responses import HTMLResponse

    rows = "".join(
        f"<tr><td>{escape(method.upper())}</td><td>{escape(path)}</td><td>{escape(data.get('summary', ''))}</td></tr>"
        for path, methods in app.openapi()["paths"].items()
        for method, data in methods.items()
    )
    return HTMLResponse(
        "<!doctype html><html lang='ru'><meta charset='utf-8'><title>CRM API</title>"
        "<h1>KORAVTO CRM API</h1><p><a href='/api/openapi.json'>OpenAPI JSON</a></p><table>"
        + rows
        + "</table></html>"
    )


def websocket_authorized(raw: str) -> bool:
    with SessionLocal() as db:
        session = db.get(LoginSession, token_hash(raw)) if raw else None
        if not session or session.expires_at <= now():
            return False
        user = db.get(User, session.user_id)
        return bool(user and user.active and not user.must_change_password)


@app.websocket("/api/ws")
async def websocket_updates(websocket: WebSocket):
    raw = websocket.cookies.get(auth.COOKIE, "")
    if websocket.headers.get("origin") != config().public_url or not websocket_authorized(raw):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    client = Redis.from_url(config().redis_url, socket_connect_timeout=2, socket_timeout=30)
    try:
        async with client.pubsub() as pubsub:
            await pubsub.subscribe("crm:events")
            last_check = 0.0
            while True:
                if time.monotonic() - last_check > 20:
                    if not websocket_authorized(raw):
                        await websocket.close(code=1008)
                        return
                    await websocket.send_json({"type": "ping"})
                    last_check = time.monotonic()
                item = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if item:
                    await websocket.send_json(json.loads(item["data"]))
                await asyncio.sleep(0.05)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        with __import__("contextlib").suppress(Exception):
            await websocket.close(code=1013)
    finally:
        await client.aclose()
