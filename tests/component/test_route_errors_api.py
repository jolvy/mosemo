from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.api import v1_api_router
from mosemo.exception_handlers import register_exception_handlers


def make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    return app


def test_unknown_api_path_returns_public_route_not_found_error() -> None:
    app = make_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/not-a-registered-route")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "status": "REQUEST_ROUTE_NOT_FOUND",
            "code": 404,
            "message": "API route not found",
            "details": [],
        }
    }
    assert "/api/v1/not-a-registered-route" not in response.text


def test_wrong_method_on_known_api_route_returns_public_method_error() -> None:
    app = make_app()

    with TestClient(app) as client:
        response = client.post("/api/v1/accounts/me")

    assert response.status_code == 405
    assert response.json() == {
        "error": {
            "status": "REQUEST_METHOD_NOT_ALLOWED",
            "code": 405,
            "message": "Method not allowed",
            "details": [],
        }
    }
    assert response.headers["allow"] == "GET"
    assert "POST" not in response.text
    assert "GET" not in response.text
