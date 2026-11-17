import argparse
import logging
import os

import uvicorn

from epc_api.config import ConfigurationError, load_settings
from epc_api.errors import EPCError
from epc_api.http import EPCSession


def main():
    parser = argparse.ArgumentParser(description="Renault Korea EPC API")
    parser.add_argument("--config", default=os.environ.get("EPC_CONFIG", "config.ini"))
    parser.add_argument(
        "--check-login", action="store_true", help="Проверить вход через первый прокси"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings(args.config)
        if args.check_login:
            session = EPCSession(settings, settings.proxies[0] if settings.proxies else None)
            try:
                session.login()
                print("Авторизация EPC успешна. Поиск деталей этой командой не проверяется.")
            finally:
                session.close()
            return
        from epc_api.app import create_app

        app = create_app(settings)
    except (ConfigurationError, EPCError) as exc:
        parser.exit(1, f"{exc}\n")
    # One process owns the round-robin counter and all per-proxy session locks.
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()
