import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from epc_api.catalog import load_profile
from epc_api.config import Settings, load_settings
from epc_api.errors import EPCError
from epc_api.models import ErrorResponse, PartsRequest, PartsResponse
from epc_api.service import PartsService

logger = logging.getLogger(__name__)
basic = HTTPBasic(auto_error=False)


def create_app(settings: Settings | None = None, service: PartsService | None = None) -> FastAPI:
    settings = settings or load_settings(Path(os.environ.get("EPC_CONFIG", "config.ini")))
    service = service or PartsService(settings, load_profile(settings.catalog_path))

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(
        title="Renault Korea EPC API",
        version="0.1.0",
        lifespan=lifespan,
        description="Поиск запчастей EPC через HTTP-запросы с ротацией прокси.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def authorize(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]):
        username = credentials.username if credentials else ""
        password = credentials.password if credentials else ""
        user_ok = secrets.compare_digest(username.encode(), settings.api_username.encode())
        password_ok = secrets.compare_digest(password.encode(), settings.api_password.encode())
        if not (credentials and user_ok and password_ok):
            raise HTTPException(
                401,
                "Неверный логин или пароль API.",
                headers={"WWW-Authenticate": 'Basic realm="EPC API"'},
            )

    @app.exception_handler(EPCError)
    async def epc_error(request: Request, exc: EPCError):
        logger.warning("EPC request failed: code=%s", exc.code)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "part_number": getattr(exc, "part_number", None),
                },
            },
        )

    @app.get("/health/live", include_in_schema=False)
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", dependencies=[Depends(authorize)], include_in_schema=False)
    def ready():
        # A configuration check only; it does not claim that remote credentials work.
        return JSONResponse(
            status_code=200 if service.configured else 503,
            content={"catalog_configured": service.configured, "upstream_checked": False},
        )

    @app.post(
        "/parts",
        response_model=PartsResponse,
        dependencies=[Depends(authorize)],
        responses={code: {"model": ErrorResponse} for code in (404, 502, 503, 504)},
    )
    def parts(body: PartsRequest):
        if len(body.part_numbers) > settings.max_batch_size:
            raise HTTPException(422, f"Разрешено до {settings.max_batch_size} номеров за запрос.")
        return service.lookup(body.part_numbers)

    @app.get("/openapi.json", dependencies=[Depends(authorize)], include_in_schema=False)
    def openapi():
        schema = app.openapi()
        if prefix := os.getenv("EPC_PUBLIC_PREFIX", ""):
            schema = dict(schema, servers=[{"url": prefix}])
        return schema

    @app.get("/docs", dependencies=[Depends(authorize)], include_in_schema=False)
    def docs():
        return get_swagger_ui_html(openapi_url="/openapi.json", title="EPC API")

    return app
