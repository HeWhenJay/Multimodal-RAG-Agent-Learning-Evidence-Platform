from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentTaskType = Literal["pure_read_query", "planning_task", "mutation_task"]


class AgentTaskStartRequest(BaseModel):
    """Worker 提交给统一图的本地任务快照，不包含跨服务地址或内部令牌。"""

    taskId: str = Field(..., min_length=1)
    taskType: AgentTaskType
    input: dict[str, Any] = Field(default_factory=dict)
    threadId: str | None = None


class AgentTaskStartResponse(BaseModel):
    taskId: str
    threadId: str
    accepted: bool
    status: str
    errorCode: str | None = None
    errorMessage: str | None = None


class AgentTaskResumeRequest(BaseModel):
    """Worker 从持久化审批恢复统一图所需的最小数据。"""

    taskId: str = Field(..., min_length=1)
    taskType: AgentTaskType
    input: dict[str, Any] = Field(default_factory=dict)
    threadId: str | None = None
    reviewType: str
    decision: str
    decisionPayload: dict[str, Any] = Field(default_factory=dict)


class AgentToolCallEvent(BaseModel):
    id: str
    toolName: str
    toolType: str = "READ"
    status: str
    response: dict[str, Any] = Field(default_factory=dict)
    ownershipVerified: bool | None = None
    scope: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None


class AgentTaskEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    eventType: str
    status: str
    pythonThreadId: str
    toolCall: AgentToolCallEvent | None = None
    draft: dict[str, Any] = Field(default_factory=dict)
    finalResult: dict[str, Any] | None = Field(default=None, alias="final")
    reviewRequest: dict[str, Any] | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
