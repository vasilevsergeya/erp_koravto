from fastapi.testclient import TestClient

from mobis_api.main import app, normalize_part_number


def test_health() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_requires_basic_auth() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/parts/338002F600/price")

    assert response.status_code == 401


def test_part_number_normalizes_supplier_search():
    assert normalize_part_number(" 58310 - g7a30 ") == "58310G7A30"
