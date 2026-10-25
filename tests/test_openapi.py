"""OpenAPI 元数据与示例测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from smart_roadtrip.main import app

client = TestClient(app)


def test_openapi_metadata_and_examples() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200

    spec = response.json()
    assert spec["info"]["title"] == "基于多智能体协作的旅行规划系统 API"
    assert spec["info"]["version"] == "1.0.0"

    tags = {item["name"] for item in spec.get("tags", [])}
    assert {"chat", "trips", "admin", "ops"} <= tags

    paths = spec["paths"]
    for path in (
        "/chat",
        "/trips",
        "/trips/{thread_id}",
        "/trips/usage",
        "/admin/usage",
    ):
        assert path in paths

    assert paths["/chat"]["post"]["summary"] == "创建行程"
    assert (
        paths["/admin/usage"]["get"]["summary"]
        == "管理员跨租户用量汇总"
    )

    chat_schema = spec["components"]["schemas"]["ChatRequest"]
    assert chat_schema.get("examples")
