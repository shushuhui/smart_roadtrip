"""行程相关数据模型。

本模块只依赖标准库与 pydantic，不导入项目内其他包，作为全局数据契约。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid4().hex[:12]}"


class AgentName(str, Enum):
    """子 Agent 标识，与编排图节点名保持一致。"""

    ORCHESTRATOR = "orchestrator"
    ROUTE = "route"
    WEATHER = "weather"
    ENERGY = "energy"
    KNOWLEDGE = "knowledge"
    SAFETY = "safety"
    GROUP = "group"
    REPORT = "report"


class AgentStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class TripStatus(str, Enum):
    PLANNING = "planning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    REVISING = "revising"
    APPROVED = "approved"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    COMPLETED = "completed"


class EnergyType(str, Enum):
    EV = "ev"
    PHEV = "phev"
    ICE = "ice"


class TravelPace(str, Enum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    INTENSIVE = "intensive"


class DrivingLoad(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


# ---------------------------------------------------------------------------
# 用户输入
# ---------------------------------------------------------------------------


class GeoPoint(BaseModel):
    name: str = ""
    lng: float
    lat: float


class TravelPreferences(BaseModel):
    pace: TravelPace = TravelPace.BALANCED
    interests: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class VehicleInfo(BaseModel):
    energy_type: EnergyType = EnergyType.ICE
    rated_range_km: float = Field(default=500.0, gt=0)
    current_soc: float = Field(default=1.0, ge=0.0, le=1.0)
    seats: int = Field(default=5, ge=1)

    @field_validator("current_soc", mode="before")
    @classmethod
    def _percent_to_ratio(cls, value: object) -> object:
        """兼容 80 这类百分比写法；0-1 的小数保持不变。"""

        if isinstance(value, (int, float)) and 1 < value <= 100:
            return value / 100
        return value


class Traveler(BaseModel):
    name: str = "同行人"
    is_driver: bool = False
    budget_cny: float | None = Field(default=None, ge=0.0)
    preferences: list[str] = Field(default_factory=list)


class TripRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "origin": "成都",
                    "destination": "稻城亚丁",
                    "days": 4,
                    "start_date": "2026-11-01",
                    "travelers": [
                        {"name": "我", "is_driver": True, "preferences": ["雪山"]}
                    ],
                    "vehicle": {
                        "energy_type": "ev",
                        "rated_range_km": 500,
                        "current_soc": 0.8,
                    },
                    "preferences": {
                        "pace": "balanced",
                        "interests": ["雪山", "摄影"],
                    },
                    "budget_cny": 8000,
                }
            ]
        },
    )

    request_id: str = Field(default_factory=lambda: _new_id("req_"))
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    days: int = Field(ge=1, le=30)
    start_date: date
    travelers: list[Traveler] = Field(min_length=1)
    vehicle: VehicleInfo = Field(default_factory=VehicleInfo)
    preferences: TravelPreferences = Field(default_factory=TravelPreferences)
    budget_cny: float | None = Field(default=None, ge=0.0)
    notes: str = ""

    @property
    def needs_charging(self) -> bool:
        return self.vehicle.energy_type in (EnergyType.EV, EnergyType.PHEV)


# ---------------------------------------------------------------------------
# 子 Agent 产出
# ---------------------------------------------------------------------------


class RouteOption(BaseModel):
    option_id: str = Field(default_factory=lambda: _new_id("route_"))
    name: str
    distance_km: float = Field(ge=0.0)
    duration_min: int = Field(ge=0)
    tolls_cny: float = Field(default=0.0, ge=0.0)
    driving_load: DrivingLoad = DrivingLoad.MEDIUM
    highlights: list[str] = Field(default_factory=list)
    # 高德返回的路线折线；占位路线为空。
    polyline: list[GeoPoint] = Field(default_factory=list)
    is_placeholder: bool = False


class WeatherWindow(BaseModel):
    day_index: int = Field(ge=1)
    condition: str
    temp_low_c: float
    temp_high_c: float
    precipitation_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    warning: str | None = None


class ChargingStop(BaseModel):
    day_index: int = Field(ge=1)
    name: str
    location: GeoPoint | None = None
    arrival_soc: float = Field(ge=0.0, le=1.0)
    target_soc: float = Field(ge=0.0, le=1.0)
    charge_minutes: int = Field(ge=0)
    # 实时价格/排队需要商业充电网络接口；POI 数据只能提供站点位置。
    queue_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    price_per_kwh: float | None = Field(default=None, ge=0.0)
    source: str = "placeholder"
    notes: str = ""


class KnowledgeSnippet(BaseModel):
    topic: str
    title: str
    content: str
    source: str = "占位知识库"
    tags: list[str] = Field(default_factory=list)


class SafetyAlert(BaseModel):
    alert_id: str = Field(default_factory=lambda: _new_id("alert_"))
    severity: AlertSeverity = AlertSeverity.INFO
    category: str
    message: str
    recommendation: str
    requires_user_confirmation: bool = True


class GroupPlan(BaseModel):
    group_size: int = Field(ge=1)
    merged_interests: list[str] = Field(default_factory=list)
    votes: dict[str, int] = Field(default_factory=dict)
    cost_split: dict[str, float] = Field(default_factory=dict)
    notes: str = ""


class RescueResource(BaseModel):
    """沿途医疗/维修 POI；只代表地点信息，不代表实时可用性。"""

    name: str
    category: str
    location: GeoPoint | None = None
    address: str = ""
    phone: str = ""
    source: str = "amap_poi"
    notes: str = ""


class Activity(BaseModel):
    start_time: str = ""
    title: str
    kind: str = "游览"
    location: GeoPoint | None = None
    duration_hours: float = Field(default=1.0, ge=0.0)
    description: str = ""


class DayPlan(BaseModel):
    day_index: int = Field(ge=1)
    date: date
    title: str
    activities: list[Activity] = Field(default_factory=list)
    driving_km: float = Field(default=0.0, ge=0.0)
    notes: str = ""


class CostEstimate(BaseModel):
    """粗略的能源 + 过路费估算；不含住宿、餐饮和门票。"""

    energy_cost_cny: float = Field(default=0.0, ge=0.0)
    tolls_cny: float = Field(default=0.0, ge=0.0)
    total_cny: float = Field(default=0.0, ge=0.0)
    assumptions: list[str] = Field(default_factory=list)
    is_placeholder: bool = False


class Itinerary(BaseModel):
    title: str
    summary: str = ""
    days: list[DayPlan] = Field(default_factory=list)
    total_distance_km: float = Field(default=0.0, ge=0.0)
    total_drive_hours: float = Field(default=0.0, ge=0.0)
    cost_estimate: CostEstimate | None = None
    is_placeholder: bool = False
    generated_at: datetime = Field(default_factory=_utcnow)


class AgentResult(BaseModel):
    """统一的子 Agent 返回结构。

    一个模型承载所有 Agent 的可选产出，编排层与 ReportAgent 按字段取用，
    避免为每个 Agent 维护一套重复的包装类型。
    """

    agent: AgentName
    round: int = Field(default=1, ge=1)
    status: AgentStatus = AgentStatus.OK
    summary: str = ""
    error: str | None = None
    is_placeholder: bool = False

    route_options: list[RouteOption] = Field(default_factory=list)
    weather_windows: list[WeatherWindow] = Field(default_factory=list)
    charging_stops: list[ChargingStop] = Field(default_factory=list)
    knowledge_snippets: list[KnowledgeSnippet] = Field(default_factory=list)
    safety_alerts: list[SafetyAlert] = Field(default_factory=list)
    rescue_resources: list[RescueResource] = Field(default_factory=list)
    group_plan: GroupPlan | None = None
    report_markdown: str | None = None
    data_sources: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != AgentStatus.FAILED


# ---------------------------------------------------------------------------
# 编排、版本与确认
# ---------------------------------------------------------------------------


class TripVersion(BaseModel):
    """不可变行程快照；是否生效由 orchestration state 的指针决定。"""

    version_id: str = Field(default_factory=lambda: _new_id("ver_"))
    number: int = Field(ge=1)
    reason: str
    created_at: datetime = Field(default_factory=_utcnow)
    itinerary: Itinerary


class PendingConfirmation(BaseModel):
    confirmation_id: str = Field(default_factory=lambda: _new_id("confirm_"))
    version_id: str
    summary_markdown: str
    safety_alerts: list[SafetyAlert] = Field(default_factory=list)
    question: str = "是否确认该行程方案？"
    created_at: datetime = Field(default_factory=_utcnow)


class ConfirmationDecision(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "decision": "approve",
                    "acknowledged_alert_ids": ["alert_xxx"],
                },
                {
                    "decision": "reject",
                    "feedback": "想少开一点车",
                },
            ]
        }
    )

    decision: DecisionType
    feedback: str = ""
    acknowledged_alert_ids: list[str] = Field(default_factory=list)
