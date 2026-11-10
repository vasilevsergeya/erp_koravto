import json
import logging
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class SafeJSONFormatter(logging.Formatter):
    def format(self, record):
        # Never serialize exception messages, request URLs, payloads or arbitrary args.
        event = getattr(record, "event", record.msg if record.name == "crm" else "runtime")
        output = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": event,
            "source": record.name,
        }
        for key in (
            "correlation_id",
            "status",
            "method",
            "route",
            "duration_ms",
            "event_id",
            "error_type",
        ):
            value = getattr(record, key, None)
            if value is not None:
                output[key] = value
        return json.dumps(output, ensure_ascii=False)


def configure_logging(service: str):
    formatter = SafeJSONFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    directory = Path("/var/log/crm")
    if directory.is_dir():
        file_handler = TimedRotatingFileHandler(
            directory / f"{service}.jsonl", when="midnight", backupCount=14, utc=True
        )
        handlers.append(file_handler)
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "arq",
        "httpx",
        "httpcore",
        "sqlalchemy",
    ):
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
