from typing import Annotated

from fastapi import Body, Cookie, FastAPI, Header, Path, Query
from fastapi.testclient import TestClient
from pydantic import Json

from mosemo.exception_handlers import register_exception_handlers
from mosemo.schemas import ApiRequestModel


def test_request_validation_handler_projects_all_request_locations() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/items/{item_id}")
    def create_item(
        item_id: Annotated[int, Path()],
        query_value: Annotated[int, Query()],
        header_value: Annotated[int, Header()],
        cookie_value: Annotated[int, Cookie()],
        body: Annotated[dict[str, int], Body()],
    ) -> dict[str, int]:
        return {
            "item_id": item_id,
            "query_value": query_value,
            "header_value": header_value,
            "cookie_value": cookie_value,
            "body": body["value"],
        }

    with TestClient(app) as client:
        client.cookies.set("cookie_value", "not-an-integer")
        response = client.post(
            "/items/not-an-integer",
            params={"query_value": "not-an-integer"},
            headers={"header-value": "not-an-integer"},
            json={"value": "not-an-integer"},
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["status"] == "INVALID_ARGUMENT"
    assert error["code"] == 422
    assert {detail["loc"][0] for detail in error["details"]} == {
        "path",
        "query",
        "header",
        "cookie",
        "body",
    }
    assert all(set(detail) == {"loc", "msg", "type"} for detail in error["details"])


def test_nested_unexpected_fields_preserve_input_name_and_full_path() -> None:
    class ItemRequest(ApiRequestModel):
        item_value: int

    class ItemsRequest(ApiRequestModel):
        item_list: list[ItemRequest]

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/items")
    def create_items(body: ItemsRequest) -> None:
        pass

    with TestClient(app) as client:
        response = client.post(
            "/items",
            json={
                "itemList": [
                    {"itemValue": 1},
                    {
                        "itemValue": 2,
                        "secret_field_name": "secret-value",
                        "AnotherSecretName": "another-secret-value",
                    },
                ]
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {
            "loc": ["body", "itemList", 1, "secret_field_name"],
            "msg": "Extra inputs are not permitted",
            "type": "extra_forbidden",
        },
        {
            "loc": ["body", "itemList", 1, "AnotherSecretName"],
            "msg": "Extra inputs are not permitted",
            "type": "extra_forbidden",
        },
    ]


def test_json_string_validation_preserves_actual_array_indexes() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/json-items")
    def create_json_items(body: list[Json[dict[str, int]]]) -> None:
        pass

    with TestClient(app) as client:
        response = client.post("/json-items", json=["{}", "{"])

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {
            "loc": ["body", 1],
            "msg": "Invalid JSON: EOF while parsing an object at line 1 column 1",
            "type": "json_invalid",
        }
    ]
