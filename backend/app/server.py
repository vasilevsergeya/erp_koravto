import uvicorn

from app.core.logging import configure_logging

configure_logging("api")
uvicorn.run(
    "app.main:app",
    host="0.0.0.0",
    port=8000,
    access_log=False,
    proxy_headers=True,
    forwarded_allow_ips="*",
    log_config=None,
)
