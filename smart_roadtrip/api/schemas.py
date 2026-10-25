"""API 请求与响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from smart_roadtrip.schemas.trip import (
    Itinerary,
    PendingConfirmation,
    TripRequest,
    TripStatus,
    TripVersion,
)


class ChatRequest(BaseModel):
    """创建行程规划的请求。

    支持自然语言 ``message`` 或结构化 ``trip``，二者只能提供一个。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "从成都到稻城亚丁玩4天，两个人，开电车",
                },
                {
                    "thread_id": "optional-thread-id",
                    "trip": {
                        "origin": "成都",
                        "destination": "稻城亚丁",
                        "days": 4,
                        "start_date": "2026-11-01",
                        "travelers": [{"name": "我", "is_driver": True}],
                        "vehicle": {"energy_type": "ev"},
                    },
                },
            ]
        }
    )

    thread_id: str | None = None
    trip: TripRequest | None = None
    message: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ChatRequest":
        if (self.trip is None) == (self.message is None):
            raise ValueError("trip 与 message 必须且只能提供一个。")
        return self


class TripStateView(BaseModel):
    """对外的行程状态视图，不暴露 Agent 内部原始结果。"""

    thread_id: str
    status: TripStatus
    round: int = 0
    revision_count: int = 0
    tasks: list[str] = Field(default_factory=list)
    current_version_id: str | None = None
    confirmed_version_id: str | None = None
    versions: list[TripVersion] = Field(default_factory=list)
    itinerary: Itinerary | None = None
    pending_confirmation: PendingConfirmation | None = None
    report_markdown: str | None = None
    errors: list[str] = Field(default_factory=list)

    @classmethod
    def from_state(cls, thread_id: str, state: dict) -> "TripStateView":
        return cls(
            thread_id=thread_id,
            status=state.get("status", TripStatus.PLANNING),
            round=state.get("round", 0),
            revision_count=state.get("revision_count", 0),
            tasks=list(state.get("tasks") or []),
            current_version_id=state.get("current_version_id"),
            confirmed_version_id=state.get("confirmed_version_id"),
            versions=list(state.get("versions") or []),
            itinerary=state.get("itinerary"),
            pending_confirmation=state.get("pending_confirmation"),
            report_markdown=state.get("report_markdown"),
            errors=list(state.get("errors") or []),
        )


class ChatResponse(BaseModel):
    thread_id: str
    awaiting_confirmation: bool
    message: str = ""
    trip: TripRequest
    state: TripStateView


class TripSummary(BaseModel):
    thread_id: str
    created_at: datetime
    status: TripStatus
    origin: str = ""
    destination: str = ""
    days: int = 0
    current_version_id: str | None = None
    confirmed_version_id: str | None = None


class UsageResponse(BaseModel):
    used_24h: int
    limit: int
    remaining: int | None = None
    total_threads: int = 0
    recent_by_status: dict[str, int] = Field(default_factory=dict)
    recent_estimated_cost_cny: float = 0.0
    last_created_at: datetime | None = None


class AuditEvent(BaseModel):
    event: str
    thread_id: str = ""
    detail: str = ""
    created_at: datetime


class TenantSummary(BaseModel):
    owner: str
    total_threads: int
    last_created_at: datetime | None = None
    by_status: dict[str, int] = Field(default_factory=dict)
    estimated_cost_cny: float = 0.0


class AdminUsageResponse(BaseModel):
    total_tenants: int
    total_threads: int
    tenants: list[TenantSummary] = Field(default_factory=list)
    generated_at: datetime


class ReportResponse(BaseModel):
    thread_id: str
    version_id: str | None = None
    markdown: str


class JournalResponse(BaseModel):
    thread_id: str
    version_id: str | None = None
    markdown: str
    generated_by: str
