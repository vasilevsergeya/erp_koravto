from .config import load_settings
from .main import app


def main() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
