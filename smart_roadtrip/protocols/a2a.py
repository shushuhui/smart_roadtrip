"""A2A：Agent 间协作协议。

编排层向子 Agent 委派任务时使用 :class:`A2AMessage`，子 Agent 返回
:class:`A2AResponse`。消息只承载任务/结果描述，不直接修改编排状态。
MCP 负责 Agent 到工具的调用，A2A 负责 Agent 之间的协作，两者正交。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from smart_roadtrip.schemas.trip import AgentName


class A2AStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"


class A2AMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)
    sender: AgentName
    recipient: AgentName
    task: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class A2AResponse(BaseModel):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    correlation_id: str
    sender: AgentName
    recipient: AgentName
    status: A2AStatus
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def new_task(
    *,
    sender: AgentName,
    recipient: AgentName,
    task: str,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> A2AMessage:
    data: dict[str, Any] = {
        "sender": sender,
        "recipient": recipient,
        "task": task,
        "payload": payload or {},
    }
    if correlation_id:
        data["correlation_id"] = correlation_id
    return A2AMessage(**data)


def new_result(
    task: A2AMessage,
    *,
    sender: AgentName,
    status: A2AStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> A2AResponse:
    return A2AResponse(
        correlation_id=task.correlation_id,
        sender=sender,
        recipient=task.sender,
        status=status,
        result=result or {},
        error=error,
    )
