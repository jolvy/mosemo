from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.exception_handlers import register_exception_handlers
from mosemo.exceptions import MosemoApiException


def test_mosemo_api_exception_handler_returns_json_response() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/error")
    def error() -> None:
        raise MosemoApiException(
            status_code=418,
            detail="Expected API error",
            headers={"X-Error": "mosemo"},
        )

    with TestClient(app) as client:
        response = client.get("/error")

    assert response.status_code == 418
    assert response.json() == {"detail": "Expected API error"}
    assert response.headers["x-error"] == "mosemo"
