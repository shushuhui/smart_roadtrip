"""基于多智能体协作的旅行规划系统 HTTP 客户端。

面向车机/小程序后端等集成方，封装常用 API，不依赖服务端内部类型。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class RoadtripAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class RoadtripClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        api_key: str | None = None,
        api_key_header: str = "X-API-Key",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_key_header = api_key_header
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def __enter__(self) -> "RoadtripClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------
    # 基础接口
    # ------------------------------------------------------------------

    def health(self) -> dict:
        return self._json("GET", "/health")

    def ready(self) -> dict:
        return self._json("GET", "/ready")

    def create_plan(
        self,
        *,
        trip: Any | None = None,
        message: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        if (trip is None) == (message is None):
            raise ValueError("trip 与 message 必须且只能提供一个。")

        payload: dict[str, Any] = {}
        if thread_id:
            payload["thread_id"] = thread_id
        if trip is not None:
            payload["trip"] = (
                trip.model_dump(mode="json")
                if hasattr(trip, "model_dump")
                else trip
            )
        else:
            payload["message"] = message
        return self._json("POST", "/chat", json=payload)

    def list_trips(self, *, limit: int = 20, offset: int = 0) -> list[dict]:
        return self._json(
            "GET",
            "/trips",
            params={"limit": limit, "offset": offset},
        )

    def get_trip(self, thread_id: str) -> dict:
        return self._json("GET", self._trip_path(thread_id))

    def confirm(
        self,
        thread_id: str,
        *,
        decision: str = "approve",
        feedback: str = "",
        acknowledged_alert_ids: list[str] | None = None,
    ) -> dict:
        return self._json(
            "POST",
            f"{self._trip_path(thread_id)}/confirm",
            json={
                "decision": decision,
                "feedback": feedback,
                "acknowledged_alert_ids": acknowledged_alert_ids or [],
            },
        )

    def report(self, thread_id: str) -> dict:
        return self._json("GET", f"{self._trip_path(thread_id)}/report")

    def journal(self, thread_id: str) -> dict:
        return self._json("POST", f"{self._trip_path(thread_id)}/journal")

    def export(
        self,
        thread_id: str,
        *,
        export_format: str = "md",
        include_journal: bool = False,
    ) -> bytes:
        response = self._request(
            "GET",
            f"{self._trip_path(thread_id)}/export",
            params={
                "format": export_format,
                "include_journal": include_journal,
            },
        )
        return response.content

    def usage(self) -> dict:
        return self._json("GET", "/trips/usage")

    def audit(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        return self._json(
            "GET",
            "/trips/audit",
            params={"limit": limit, "offset": offset},
        )

    def delete(self, thread_id: str) -> None:
        self._request("DELETE", self._trip_path(thread_id))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _trip_path(self, thread_id: str) -> str:
        return f"/trips/{quote(thread_id, safe='')}"

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {self.api_key_header: self.api_key}
        return {}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            **kwargs,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise RoadtripAPIError(response.status_code, detail)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, path, **kwargs).json()
