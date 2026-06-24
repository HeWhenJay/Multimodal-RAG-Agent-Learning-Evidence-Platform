from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentTaskType = Literal["pure_read_query", "planning_task", "mutation_task"]


class AgentTaskStartRequest(BaseModel):
    taskId: str = Field(..., min_length=1)
    taskType: AgentTaskType
    input: dict[str, Any] = Field(default_factory=dict)
    callbackUrl: str = Field(..., min_length=1)
    javaToolGatewayBaseUrl: str = Field(..., min_length=1)
    threadId: str | None = None


class AgentTaskStartResponse(BaseModel):
    taskId: str
    threadId: str
    accepted: bool
    status: str
    errorCode: str | None = None
    errorMessage: str | None = None


class AgentTaskResumeRequest(BaseModel):
    taskId: str = Field(..., min_length=1)
    taskType: AgentTaskType
    input: dict[str, Any] = Field(default_factory=dict)
    callbackUrl: str = Field(..., min_length=1)
    javaToolGatewayBaseUrl: str = Field(..., min_length=1)
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
