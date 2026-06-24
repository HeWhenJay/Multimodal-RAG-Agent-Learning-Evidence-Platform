# Agent 接口文档

更新日期：2026-06-21

## 变更摘要

阶段 0 新增 Agent 第二阶段契约和表结构说明。阶段 1 已实现 Java `agent_task` 创建/查询、HTTP 级 `/api/internal/agent/tools/read` 只读 Tool Gateway、`RagService.queryNonPersistent()` 和严格 `X-Agent-Internal-Token` 校验。阶段 2 已实现 Python LangGraph 纯只读闭环、Java 调 Python Agent client、Python 回写 Java events，以及前端 `/agent` 最小页面。阶段 3 新增规划类 `planning_task` 的计划审批、只读 evidence 对齐、能力缺口分析和输出审批闭环。阶段 4 新增 Agent 自身范围内的 CRUD 审批、before/after snapshot、幂等键和撤销窗口。阶段 5 新增 `web_search_probe` 联网参考工具。阶段 6 的旧 `resume_template_fill` 仅保留兼容说明，当前用户可参与的简历修改必须走 `/resume-template` 图片预览、区域确认、补丁草稿、校验和导出流程。阶段 7 新增 Agent 记忆最小闭环，详细接口见 `docs/api/agent-memory.md`；当前仍未实现 MCP 或 `web_page_fetcher`。

核心边界：

- React 只调用 Java `/api/agent/*`。
- Java 是唯一对外 API、登录用户、业务权限、审计、幂等和错误映射边界。
- Python Agent 只负责编排、计划、工具观察整合、草稿生成和 citation guard。
- Python Agent 只能通过 Java Tool Gateway 调业务能力，不能直连数据库、对象存储、Java Mapper、Python RAG `/internal/*` 或 `create_rag_store()`。
- Python Agent 只能通过 Java Tool Gateway 使用 Agent 记忆能力；记忆状态、确认、归档、删除和审计以 Java 为准。
- 普通上传、分片上传和确定性 RAG 入库不纳入 Agent 工具。
- 当前版本未实现授权表，`explicitGrant` 只是预留语义；除 `ownerUserId == currentUserId` 外全部拒绝。

## 状态机

### 任务类型

| 值 | 含义 | 阶段 |
| --- | --- | --- |
| `pure_read_query` | 资料状态、evidence 读取、RAG 探针、覆盖诊断等只读任务 | 阶段 1-2 |
| `planning_task` | JD/简历适配、学习路线、证据质量诊断等需要计划或输出确认的任务 | 阶段 3 |
| `mutation_task` | 重建索引、保存草稿、保存学习计划、取消任务、撤销等变更任务 | 阶段 4 |

### `agent_task.status`

| 状态 | 含义 |
| --- | --- |
| `CREATED` | Java 已创建任务，等待启动 Python Agent 或本地只读网关 |
| `RUNNING` | Agent 正在生成计划、调用工具或整合结果 |
| `WAITING_TOOL_RESULT` | 已发起工具调用，等待 Tool Gateway 或 Python 回写 Observation |
| `WAITING_PLAN_REVIEW` | 等待用户确认计划，仅规划类任务使用 |
| `WAITING_CRUD_REVIEW` | 等待用户确认具体变更操作 |
| `WAITING_OUTPUT_REVIEW` | 等待用户确认规划类最终草稿 |
| `COMPLETED` | 任务完成，`finalJson` 可展示 |
| `CANCELED` | 用户取消或审批拒绝后结束 |
| `FAILED` | 任务失败，`errorCode/errorMessage` 为脱敏摘要 |

### 工具、审批和操作状态

`agent_tool_call.status`：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED` / `REJECTED`。

`agent_human_review.review_type`：`PLAN` / `CRUD` / `OUTPUT`。

`agent_human_review.status`：`PENDING` / `APPROVED` / `REJECTED` / `CHANGES_REQUESTED` / `EXPIRED`。

阶段 3 只实现 `PLAN` 和 `OUTPUT` 两类审批；二者都不授权任何 Create/Update/Delete 或业务状态变更。阶段 4 开始实现 `CRUD` 审批，但初版只允许 Agent 自身草稿保存、任务取消和撤销操作，不把普通上传、分片上传、确定性 RAG 入库或资料重建索引纳入 Agent 自动工具。

`agent_operation.status`：`PENDING_APPROVAL` / `APPLIED_UNDOABLE` / `UNDONE` / `UNDO_EXPIRED` / `FINALIZED` / `FAILED`。

撤销状态初版采用查询时流转：读取任务或操作详情时，如果 `undoDeadline` 已过且状态仍为 `APPLIED_UNDOABLE`，Java 可更新为 `UNDO_EXPIRED`；后续再补定时任务归档为 `FINALIZED`。

## 权限和安全

- 外部接口必须携带 `Authorization: Bearer <token>`。
- Java 从登录 token 解析当前用户，并写入 `agent_task.user_id`。
- 内部接口必须校验 `X-Agent-Internal-Token: ${EVIDENCE_AGENT_INTERNAL_TOKEN}`。
- `EVIDENCE_AGENT_INTERNAL_TOKEN` 为空时，Java 内部 Agent Tool Gateway 和 Python Agent API 应拒绝启动或拒绝处理内部调用，避免误开无鉴权接口。
- Java Tool Gateway 根据 `taskId` 查询 `agent_task.user_id`，不信任 Python 传入的 `userId`。
- 只读工具无需 HITL，但任何 `resourceId/documentId/materialId/operationId` 必须由 Java 做 owner 校验。
- 当前版本 `scope=current_user_or_authorized` 的实际含义是“当前用户本人资源”；`explicitGrant` 未落表前非 owner 一律返回 `AGENT_RESOURCE_FORBIDDEN`。
- 只读工具允许写脱敏 `log_event/log_error` 和 `agent_tool_call` 观测记录，不允许写业务历史或修改业务状态。
- `rag_query_probe_non_persistent` 必须走 `RagService.queryNonPersistent()` 专用分支，复用 `scopedQuery()` 覆盖 `userId/visibilityScope`，不调用 `saveSynchronousQueryHistory`，不创建 query task，不写 `rag_query_history`。

## 数据库表

阶段 0 已在 `infra/sql/init.sql` 和 `infra/sql/alter-database/20260621_0200_create_agent_tables.sql` 中声明以下表；阶段 1 已同步 `backend-java/src/main/resources/schema.sql`，用于 H2 集成测试覆盖 Java Mapper 和 HTTP 接口。

| 表 | 用途 |
| --- | --- |
| `agent_task` | Agent 任务主状态、输入、计划、草稿、最终输出和 Python checkpoint thread |
| `agent_tool_call` | 每次工具调用的请求、响应、归属校验、状态和错误摘要 |
| `agent_human_review` | 计划、CRUD、输出确认记录 |
| `agent_operation` | 可撤销变更操作、幂等键、快照引用和撤销窗口 |
| `agent_operation_snapshot` | 变更前后脱敏快照或安全恢复引用 |
| `agent_memory_item` | 当前用户 Agent 记忆元数据、状态、作用域和来源引用 |
| `agent_memory_embedding` | Python Memory Service 专用记忆检索索引 |
| `agent_memory_version` | 记忆版本、冲突、合并和替代关系 |
| `agent_memory_audit` | 记忆生命周期脱敏审计 |

`agent_operation` 的幂等唯一约束：

```text
(user_id, operation_type, resource_type, resource_id, idempotency_key)
```

快照禁止保存模型密钥、对象存储签名 URL、未脱敏长篇资料正文、未授权简历或 JD 全文。

`input_json/plan_json/draft_json/final_json/request_json/response_json/proposal_json/decision_json/snapshot_json` 采用 `TEXT` 保存脱敏 JSON 字符串，和现有 `log_event.context_json`、`rag_query_history.*_json` 保持一致，避免 Java/MyBatis 在 PostgreSQL 与 H2 测试库之间维护两套 JSONB 写入语法。

## Java DTO/VO 清单

阶段 1-4 实现时优先按以下名称落地，字段与本文档示例保持一致。

DTO：

| 类名 | 用途 |
| --- | --- |
| `AgentTaskCreateDTO` | 创建任务请求 |
| `AgentReviewDecisionDTO` | 用户提交计划、CRUD 或输出审批结果 |
| `AgentTaskCancelDTO` | 用户取消任务请求 |
| `AgentOperationUndoDTO` | 撤销窗口内回滚请求 |
| `AgentReadToolRequestDTO` | Python Agent 调 Java 只读工具网关请求 |
| `AgentMutationToolExecuteDTO` | Python Agent 调已审批变更工具请求 |
| `AgentTaskEventDTO` | Python Agent 回写任务状态、Observation、草稿和 review 请求 |

VO：

| 类名 | 用途 |
| --- | --- |
| `AgentTaskVO` | 任务摘要 |
| `AgentTaskDetailVO` | 任务详情，聚合工具调用、审批项和操作 |
| `AgentToolCallVO` | 工具调用记录 |
| `AgentHumanReviewVO` | 审批记录 |
| `AgentOperationVO` | 变更操作和撤销窗口 |
| `AgentToolDefinitionVO` | 前端可展示工具能力和审批规则 |
| `AgentToolResultVO` | 内部工具网关统一结果 |

## Java 对外接口

所有外部接口返回 `Result<T>`：

```json
{
  "code": 1,
  "msg": null,
  "data": {}
}
```

业务错误返回：

```json
{
  "code": 0,
  "msg": "AGENT_RESOURCE_FORBIDDEN：当前任务无权读取该资源",
  "data": null
}
```

### 创建 Agent 任务

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/tasks` |
| 鉴权 | Bearer Token |
| 响应 | `Result<AgentTaskVO>` |

请求示例：

```json
{
  "taskType": "pure_read_query",
  "title": "查询 Redis 学习证据",
  "input": {
    "goal": "我的知识库里 Redis 学到了什么？",
    "toolHints": ["rag_query_probe_non_persistent"],
    "resourceRefs": [
      {
        "resourceType": "material",
        "resourceId": "12"
      }
    ],
    "metadataFilter": {
      "documentType": "markdown"
    },
    "topK": 5
  }
}
```

规划类请求示例：

```json
{
  "taskType": "planning_task",
  "title": "后端实习 JD 适配分析",
  "input": {
    "goal": "分析这份后端实习 JD 和我的学习证据差距",
    "jobDescription": "岗位要求熟悉 Java、Spring Boot、Redis、MySQL，有 RAG 项目经验优先。",
    "resumeText": "资料库中已上传简历的解析摘要：做过多模态 RAG 学习证据平台，熟悉 Java 和 Python。",
    "resumeMaterialId": 18,
    "resumeMaterialTitle": "王同学-后端实习简历.pdf",
    "toolHints": [
      "resume_evidence_aligner",
      "gap_analyzer"
    ],
    "topK": 6
  }
}
```

成功响应：

```json
{
  "code": 1,
  "msg": null,
  "data": {
    "id": "agent-task-019ee6aa",
    "taskType": "pure_read_query",
    "status": "CREATED",
    "title": "查询 Redis 学习证据",
    "input": {
      "goal": "我的知识库里 Redis 学到了什么？"
    },
    "pythonThreadId": null,
    "createdAt": "2026-06-21T03:10:00+08:00",
    "updatedAt": "2026-06-21T03:10:00+08:00"
  }
}
```

阶段 2 开始，Java 创建任务后调用 Python `/internal/agent/tasks`；Python 通过 Java events 回写状态，Java 不轮询 Python。
如果 `EVIDENCE_AGENT_INTERNAL_TOKEN` 为空，Java 只创建任务并保持 `CREATED`，不会启动 Python Agent，避免本地环境误开无鉴权内部接口。
阶段 3 的 `planning_task` 创建后先由 Python 回写 `REVIEW_REQUESTED`，任务进入 `WAITING_PLAN_REVIEW`；用户确认计划后，Java 调用 Python `/internal/agent/tasks/{taskId}/resume` 继续执行只读证据对齐，随后 Python 回写 `WAITING_OUTPUT_REVIEW`；用户确认输出后任务进入 `COMPLETED`。

### 查询任务详情

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/agent/tasks/{taskId}` |
| 鉴权 | Bearer Token |
| 响应 | `Result<AgentTaskDetailVO>` |

响应示例：

```json
{
  "code": 1,
  "msg": null,
  "data": {
    "id": "agent-task-019ee6aa",
    "taskType": "pure_read_query",
    "status": "COMPLETED",
    "plan": {},
    "draft": {},
    "final": {
      "answer": "Redis 相关证据主要集中在缓存淘汰、持久化和分布式锁。",
      "evidenceIds": ["material-12-3", "material-12-8"],
      "riskLevel": "LOW"
    },
    "toolCalls": [
      {
        "id": "tool-call-001",
        "toolName": "rag_query_probe_non_persistent",
        "toolType": "READ",
        "status": "SUCCEEDED",
        "ownershipVerified": true,
        "scope": "current_user_or_authorized",
        "response": {
          "evidenceCount": 2
        },
        "createdAt": "2026-06-21T03:10:02+08:00",
        "updatedAt": "2026-06-21T03:10:04+08:00"
      }
    ],
    "reviews": [],
    "operations": []
  }
}
```

规划类任务详情示例：

```json
{
  "code": 1,
  "msg": null,
  "data": {
    "id": "agent-task-019ee6bb",
    "taskType": "planning_task",
    "status": "WAITING_OUTPUT_REVIEW",
    "plan": {
      "title": "后端实习 JD 适配分析计划",
      "steps": [
        "读取当前用户 RAG 证据",
        "对齐 JD 要求与简历证据",
        "生成能力缺口和学习建议"
      ],
      "tools": ["rag_query_probe_non_persistent", "resume_evidence_aligner", "gap_analyzer"],
      "requiresOutputReview": true
    },
    "draft": {
      "matchSummary": "当前证据支持 Java/RAG 项目经验，Redis 证据偏弱。",
      "alignment": [
        {"requirement": "Java/Spring Boot", "status": "supported", "evidenceIds": ["material-1-2"]},
        {"requirement": "Redis", "status": "weak", "evidenceIds": []}
      ],
      "gaps": [
        {"skill": "Redis", "priority": "HIGH", "suggestion": "补充缓存淘汰、持久化和分布式锁项目证据"}
      ]
    },
    "reviews": [
      {
        "id": "review-001",
        "reviewType": "OUTPUT",
        "status": "PENDING",
        "proposal": {
          "summary": "确认后把当前草稿作为最终输出展示，不写业务数据"
        }
      }
    ],
    "operations": []
  }
}
```

### 提交审批结果

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/tasks/{taskId}/reviews/{reviewId}/decide` |
| 鉴权 | Bearer Token |
| 响应 | `Result<AgentTaskDetailVO>` |

请求示例：

```json
{
  "decision": "APPROVED",
  "comment": "同意按该计划继续执行",
  "changes": {}
}
```

规则：

- 只能审批当前用户自己的任务。
- `PLAN` 审批只确认目标和工具路线，不授权任何写操作。
- `OUTPUT` 审批只确认规划类草稿可作为最终输出；无保存意图时 Java 可直接把 `draftJson` 复制为 `finalJson`，有 `saveDraft=true` 或保存类 `toolHints` 时恢复 Python 生成 `CRUD` 审批。
- `CRUD` 审批必须绑定具体 `operationType/resourceType/resourceId/idempotencyKey/beforeSnapshotRef`。
- 审批不是幂等执行入口；变更执行仍由内部 mutation Tool Gateway 二次校验。
- 阶段 4 初版 `CRUD` 审批只允许 `RESUME_REVISION_SAVE`、`JD_PLAN_SAVE` 和 `TASK_CANCEL` 三类 Agent 自身范围内变更；`MATERIAL_REINDEX` 保持预留，不自动执行。

### 取消任务

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/tasks/{taskId}/cancel` |
| 鉴权 | Bearer Token |
| 响应 | `Result<AgentTaskDetailVO>` |

请求示例：

```json
{
  "reason": "用户主动取消"
}
```

取消属于状态变更。阶段 4 后，如果任务已有可撤销操作或待审批项，取消也要进入 `human_crud_review`；阶段 1-2 纯只读任务可直接标记为 `CANCELED`。

### 撤销操作

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/operations/{operationId}/undo` |
| 鉴权 | Bearer Token |
| 响应 | `Result<AgentOperationVO>` |

请求示例：

```json
{
  "idempotencyKey": "undo-agent-task-019ee6aa-operation-001",
  "reason": "用户撤销刚保存的学习计划"
}
```

规则：

- 只能撤销当前用户自己的 `APPLIED_UNDOABLE` 操作。
- 当前时间必须早于 `undoDeadline`。
- 当前阶段撤销必须由当前登录用户显式调用 Java 撤销接口，不由 Python Agent 自动发起；Java 恢复 before snapshot，并将原操作置为 `UNDONE`。
- 模型调用成本、已完成的视频高成本处理不可撤销；阶段 4 初版只回滚 Agent 任务自身状态、草稿和最终结果。

### 获取工具能力

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/agent/tools` |
| 鉴权 | Bearer Token |
| 响应 | `Result<List<AgentToolDefinitionVO>>` |

响应示例：

```json
{
  "code": 1,
  "msg": null,
  "data": [
    {
      "toolName": "material_status_reader",
      "toolType": "READ",
      "requiresReview": false,
      "approvalType": null,
      "stage": 1,
      "description": "读取当前用户资料解析状态、摘要和失败原因"
    },
    {
      "toolName": "agent_memory_candidate_save",
      "toolType": "MUTATION",
      "requiresReview": true,
      "approvalType": "CRUD",
      "stage": 7,
      "description": "用户确认后保存记忆候选并进入索引流程"
    }
  ]
}
```

## Java 内部 Tool Gateway

内部接口只允许 Python Agent 调用，必须携带 `X-Agent-Internal-Token`。

### 执行只读工具

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/internal/agent/tools/read` |
| Header | `X-Agent-Internal-Token` |
| 响应 | `AgentToolResultVO`，内部接口可不套外部 `Result<T>`，但错误体必须结构化 |

请求示例：

```json
{
  "taskId": "agent-task-019ee6aa",
  "toolCallId": "tool-call-001",
  "toolName": "material_status_reader",
  "arguments": {
    "materialId": "12"
  }
}
```

成功响应：

```json
{
  "taskId": "agent-task-019ee6aa",
  "toolCallId": "tool-call-001",
  "toolName": "material_status_reader",
  "status": "SUCCEEDED",
  "ownershipVerified": true,
  "scope": "current_user_or_authorized",
  "data": {
    "materialId": 12,
    "title": "Redis 笔记.md",
    "status": "READY",
    "parser": "markdown",
    "chunkCount": 18
  },
  "diagnostics": {}
}
```

只读工具清单：

| 工具名 | 参数 | Java 封装 |
| --- | --- | --- |
| `material_status_reader` | `materialId` | `RagService.getMaterial` |
| `material_evidence_reader` | `materialId/topK` | `RagService.listMaterialEvidences` |
| `material_preview_reader` | `materialId/source/maxChars` | `RagService.previewMaterial`，Java 控制长度和来源 |
| `rag_query_probe_non_persistent` | `question/topK/candidateMultiplier/metadataFilter` | `RagService.queryNonPersistent`，不写历史 |
| `retrieval_coverage_probe` | `question/topK/metadataFilter` | 复用非持久化查询 diagnostics，输出覆盖摘要 |
| `resume_evidence_aligner` | `jobDescription/resumeText/question/topK` | 阶段 3 由 Python 基于 Java RAG 探针结果做只读证据对齐 |
| `gap_analyzer` | `jobDescription/resumeText/alignment` | 阶段 3 由 Python 基于已授权 evidence 和草稿生成能力缺口 |
| `evidence_quality_auditor` | `alignment/evidenceIds` | 阶段 3 由 Python 检查证据充分性和风险等级 |
| `web_search_probe` | `query/maxResults/searchDepth/topic` | 阶段 5 由 Java 调 Tavily Search API，返回联网参考，不写 RAG evidence |
| `agent_memory_retriever` | `query/topK/namespaces/memoryTypes` | 阶段 7 按当前任务 owner 检索可注入记忆 |
| `agent_memory_candidate_proposer` | `taskInput/draft/final/toolObservations` | 阶段 7 生成待确认记忆候选，不落库 |
| `resume_template_fill` | `templatePath/contentMap/outputDir` | 仅保留兼容，不作为当前前端入口；用户可参与的 DOCX 修改走 `/api/rag/resume/templates/*` |

`utc_time_provider` 是 Python 本地系统工具，不调用 Java Gateway，不访问用户数据。

只读错误响应示例：

```json
{
  "taskId": "agent-task-019ee6aa",
  "toolCallId": "tool-call-001",
  "toolName": "material_status_reader",
  "status": "REJECTED",
  "ownershipVerified": false,
  "scope": "current_user_or_authorized",
  "errorCode": "AGENT_RESOURCE_FORBIDDEN",
  "errorMessage": "当前任务无权读取该资料",
  "retryable": false
}
```

### 执行已审批变更工具

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/internal/agent/tools/mutation/execute` |
| Header | `X-Agent-Internal-Token` |
| 阶段 | 阶段 4 |

请求必须包含：

```json
{
  "taskId": "agent-task-019ee6aa",
  "toolCallId": "tool-call-010",
  "approvalId": "review-001",
  "operationId": "operation-001",
  "toolName": "jd_learning_plan_save",
  "idempotencyKey": "save-plan-agent-task-019ee6aa-v1",
  "arguments": {
    "resourceType": "jd_learning_plan",
    "resourceId": "report-12",
    "payload": {}
  }
}
```

Java 必须校验 `approvalId` 已经由当前用户批准、`operationId` 属于当前任务、幂等键未冲突、资源仍属于当前用户，再执行变更。

阶段 4 初版支持的变更工具：

| 工具名 | operationType | resourceType | 行为 |
| --- | --- | --- | --- |
| `resume_revision_save` | `RESUME_REVISION_SAVE` | `agent_task_draft` | 将当前任务草稿按审批结果固化到 `final_json`，记录 before/after snapshot |
| `jd_learning_plan_save` | `JD_PLAN_SAVE` | `agent_task_draft` | 将学习计划草稿固化到 `final_json`，记录 before/after snapshot |
| `agent_task_cancel_request` | `TASK_CANCEL` | `agent_task` | 将当前任务标记为 `CANCELED`，记录取消前后状态 |
| `agent_memory_candidate_save` | `AGENT_MEMORY_CANDIDATE_SAVE` | `agent_memory` | 保存待确认记忆候选；显式授权后可进入索引流程 |

`material_reindex_request` 仍需后续接入资料重建链路和成本提示，本阶段不执行。

撤销窗口当前不作为 Python Agent 可直接选择的 Tool Gateway 工具；前端通过 Java `POST /api/agent/operations/{operationId}/undo` 请求恢复 before snapshot。

执行成功响应示例：

```json
{
  "taskId": "agent-task-019ee6bb",
  "toolCallId": "tool-call-010",
  "toolName": "jd_learning_plan_save",
  "status": "SUCCEEDED",
  "ownershipVerified": true,
  "scope": "current_user_or_authorized",
  "data": {
    "operationId": "operation-001",
    "status": "APPLIED_UNDOABLE",
    "beforeSnapshotRef": "agent-operation-snapshot:snapshot-before-001",
    "afterSnapshotRef": "agent-operation-snapshot:snapshot-after-001",
    "undoDeadline": "2026-06-21T16:20:00+08:00"
  },
  "retryable": false
}
```

### 联网参考工具

阶段 5 初版只实现 `web_search_probe`。该工具仍归类为只读工具，由 Python Agent 通过 Java Read Tool Gateway 调用，Java 读取 `evidence.tools.tavily.api-key` 并调用 Tavily Search API。Tavily 官方 Search API 使用 `POST https://api.tavily.com/search`，通过 Bearer API Key 鉴权，常用参数包括 `query`、`search_depth`、`topic` 和 `max_results`。

请求参数示例：

```json
{
  "taskId": "agent-task-019ee6bb",
  "toolCallId": "tool-call-web-001",
  "toolName": "web_search_probe",
  "arguments": {
    "query": "字节跳动 后端实习 Redis RAG 技能趋势",
    "maxResults": 5,
    "searchDepth": "basic",
    "topic": "general"
  }
}
```

成功响应 `data` 示例：

```json
{
  "query": "字节跳动 后端实习 Redis RAG 技能趋势",
  "retrievedAt": "2026-06-21T16:40:00+08:00",
  "requestId": "123e4567-e89b-12d3-a456-426614174111",
  "responseTime": "1.67",
  "results": [
    {
      "title": "示例网页标题",
      "sourceUrl": "https://example.com/page",
      "summary": "Tavily 返回的摘要片段，供 Agent 作为外部参考。",
      "score": 0.82,
      "confidence": "HIGH",
      "retrievedAt": "2026-06-21T16:40:00+08:00"
    }
  ]
}
```

规则：

- 外部搜索结果只作为参考上下文，不写入 `learning_material`、`rag_document`、`rag_evidence` 或 `rag_query_history`。
- 未配置 `TAVILY_API_KEY` 时返回 `AGENT_TAVILY_NOT_CONFIGURED`，`retryable=true`，规划类任务可继续使用本地 RAG evidence。
- 只允许 `searchDepth=basic/advanced/fast/ultra-fast`，默认 `basic`；`maxResults` 默认 5。
- 阶段 5 暂不实现 `web_page_fetcher`，避免在 SSRF 防护未完整落地前抓取任意 URL 正文。

### Python 回写任务事件

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/internal/agent/tasks/{taskId}/events` |
| Header | `X-Agent-Internal-Token` |
| 阶段 | 阶段 2 |

请求示例：

```json
{
  "eventType": "TOOL_OBSERVATION",
  "status": "RUNNING",
  "pythonThreadId": "agent-task-019ee6aa",
  "toolCall": {
    "id": "tool-call-001",
    "toolName": "rag_query_probe_non_persistent",
    "status": "SUCCEEDED",
    "response": {
      "evidenceCount": 2
    }
  },
  "draft": {},
  "final": null,
  "reviewRequest": null,
  "errorCode": null,
  "errorMessage": null
}
```

事件写入规则：

- `eventType=TASK_STARTED`：任务进入 `RUNNING`。
- `eventType=TOOL_OBSERVATION`：新增或更新 `agent_tool_call`。
- `eventType=REVIEW_REQUESTED`：新增 `agent_human_review` 并更新任务等待状态。
- `eventType=MUTATION_PROPOSED`：新增 `CRUD` review，并可同步创建 `agent_operation` 的 `PENDING_APPROVAL` 候选。
- `eventType=DRAFT_UPDATED`：更新 `draft_json`。
- `eventType=TASK_COMPLETED`：更新 `final_json` 和 `COMPLETED`。
- `eventType=TASK_FAILED`：更新 `errorCode/errorMessage` 和 `FAILED`。

`REVIEW_REQUESTED.reviewRequest` 示例：

```json
{
  "id": "review-001",
  "reviewType": "PLAN",
  "proposal": {
    "title": "后端实习 JD 适配分析计划",
    "steps": ["读取当前用户 RAG 证据", "生成证据对齐矩阵", "输出能力缺口"],
    "tools": ["rag_query_probe_non_persistent", "resume_evidence_aligner", "gap_analyzer"],
    "riskLevel": "LOW"
  },
  "expiresAt": "2026-06-22T03:10:00+08:00"
}
```

## Python Agent 内部接口

仅 Java 调用 Python Agent API，必须携带 `X-Agent-Internal-Token`。

### 启动任务

`POST /internal/agent/tasks`

请求示例：

```json
{
  "taskId": "agent-task-019ee6aa",
  "taskType": "pure_read_query",
  "input": {
    "goal": "我的知识库里 Redis 学到了什么？",
    "topK": 5
  },
  "callbackUrl": "http://127.0.0.1:7080/api/internal/agent/tasks/agent-task-019ee6aa/events",
  "javaToolGatewayBaseUrl": "http://127.0.0.1:7080",
  "threadId": "agent-task-019ee6aa"
}
```

响应示例：

```json
{
  "taskId": "agent-task-019ee6aa",
  "threadId": "agent-task-019ee6aa",
  "accepted": true,
  "status": "RUNNING"
}
```

### 恢复任务

`POST /internal/agent/tasks/{taskId}/resume`

Java 在用户审批后调用。Python 使用同一 `threadId` 和 checkpoint 恢复 LangGraph。

请求示例：

```json
{
  "taskId": "agent-task-019ee6bb",
  "taskType": "planning_task",
  "threadId": "agent-task-019ee6bb",
  "reviewType": "PLAN",
  "decision": "APPROVED",
  "decisionPayload": {
    "comment": "同意继续"
  },
  "input": {
    "goal": "分析这份后端实习 JD 和我的学习证据差距"
  },
  "callbackUrl": "http://127.0.0.1:7080/api/internal/agent/tasks/agent-task-019ee6bb/events",
  "javaToolGatewayBaseUrl": "http://127.0.0.1:7080"
}
```

响应示例：

```json
{
  "taskId": "agent-task-019ee6bb",
  "threadId": "agent-task-019ee6bb",
  "accepted": true,
  "status": "WAITING_OUTPUT_REVIEW"
}
```

### 阶段 6 简历模板参与式确认

当前简历修改不再让 Agent 直接决定可改范围。用户先在 `/resume-template` 完成图片预览、区域确认和约束保存，再由 Java 根据 `useConfirmedAnnotations=true` 冻结允许修改的字段边界并生成补丁草稿。旧的 `resume_template_fill` 仅作为兼容说明，不作为当前前端入口：

```json
{
  "resumeTemplateFill": {
    "status": "SUCCEEDED",
    "toolName": "resume_template_fill",
    "outputPath": "C:/.../outputs/resume-agent-task-1.docx",
    "placeholders": ["summary", "skills", "project_experience"],
    "contentMap": {
      "summary": "面向 Java 后端与 RAG 岗位的项目摘要",
      "skills": "Java / Spring Boot / Redis / RAG / Python"
    }
  }
}
```

规则：

- `useConfirmedAnnotations=true` 时，只允许使用 `editable=true`、`status=ACTIVE` 且 `fieldId` 非空的确认区域。
- 图片坐标只用于用户确认，不传给模型，也不参与 DOCX 定位。
- `resume_template_fill` 仅保留历史兼容，不作为当前 UI 的修改入口。
- 删除模板应通过 `DELETE /api/rag/resume/templates/{templateId}`，数据库级联会清理派生记录，服务层再清理对象存储文件。
- 用户确认保存生成结果时继续走阶段 4 的 `resume_revision_save` 和撤销窗口。

### 调试读取 Python checkpoint

`GET /internal/agent/tasks/{taskId}`

仅本地调试使用，仍需内部 token。返回 checkpoint 摘要，不返回资料正文、简历全文或模型密钥。

## Java-Python 调用约定

| 调用方向 | 接口 | 超时 | 重试 | 幂等 |
| --- | --- | --- | --- | --- |
| Java -> Python Agent | `/internal/agent/tasks` | 默认 10 秒接收任务 | 网络超时可重试 1 次 | 以 `taskId/threadId` 幂等 |
| Java -> Python Agent | `/internal/agent/tasks/{taskId}/resume` | 默认 10 秒 | 只对网络错误重试 1 次 | 以 `reviewId` 幂等 |
| Python Agent -> Java Read Gateway | `/api/internal/agent/tools/read` | 默认 30 秒，RAG 探针可配置到 60 秒 | 只读工具可重试 1 次 | `toolCallId` 幂等记录 |
| Python Agent -> Java Events | `/api/internal/agent/tasks/{taskId}/events` | 默认 10 秒 | 可重试 3 次 | 以 `eventType + toolCall.id/review.id` 幂等更新 |
| Python Agent -> Java Mutation Gateway | `/api/internal/agent/tools/mutation/execute` | 按工具配置 | 不自动重试不可逆操作 | 必须有 `idempotencyKey` |

Python Agent 不得直接调用 Python RAG `/internal/rag/*`。即使 Python Agent 与 Python RAG 运行在同一 FastAPI 进程，也只能通过 Java Read Tool Gateway 间接触发 RAG 查询。

## 错误码

| 错误码 | HTTP 建议 | 含义 | 可重试 |
| --- | --- | --- | --- |
| `AGENT_VALIDATION_FAILED` | 400 | 请求字段缺失、状态值非法或参数越界 | 否 |
| `AGENT_UNAUTHORIZED` | 401 | 外部登录 token 缺失或失效 | 否 |
| `AGENT_INTERNAL_TOKEN_INVALID` | 401/403 | 内部 token 缺失或错误 | 否 |
| `AGENT_TASK_NOT_FOUND` | 404 | 任务不存在 | 否 |
| `AGENT_TASK_FORBIDDEN` | 403 | 当前用户不能访问该任务 | 否 |
| `AGENT_TOOL_UNKNOWN` | 400 | 未知工具名或当前阶段未开放 | 否 |
| `AGENT_TAVILY_NOT_CONFIGURED` | 503 | Tavily API Key 未配置，联网参考不可用 | 是 |
| `AGENT_TAVILY_DOWNSTREAM_FAILED` | 502 | Tavily API 调用失败 | 视错误而定 |
| `AGENT_RESOURCE_FORBIDDEN` | 403 | 资源不属于当前用户，且 explicitGrant 未实现 | 否 |
| `AGENT_REVIEW_NOT_PENDING` | 409 | 审批不存在、已处理或已过期 | 否 |
| `AGENT_IDEMPOTENCY_CONFLICT` | 409 | 幂等键已用于不同 payload | 否 |
| `AGENT_OPERATION_UNDO_EXPIRED` | 409 | 撤销窗口已过期 | 否 |
| `AGENT_OPERATION_NOT_UNDOABLE` | 409 | 操作不存在、非本人或当前状态不可撤销 | 否 |
| `AGENT_PYTHON_UNAVAILABLE` | 502 | Python Agent 不可用 | 是 |
| `AGENT_PYTHON_TIMEOUT` | 504 | Python Agent 调用超时 | 是 |
| `AGENT_TOOL_DOWNSTREAM_FAILED` | 502 | 下游 RAG 或业务服务失败 | 视错误而定 |
| `AGENT_MEMORY_NOT_FOUND` | 404 | 记忆不存在或不属于当前用户 | 否 |
| `AGENT_MEMORY_FORBIDDEN` | 403 | 当前任务无权访问该记忆或 scope | 否 |
| `AGENT_MEMORY_SCOPE_ESCALATION` | 409 | 修改记忆时尝试放大 scope | 否 |
| `AGENT_MEMORY_INDEX_FAILED` | 502 | 记忆元数据已保存，但 Python 索引失败 | 是 |

日志脱敏规则沿用 `docs/api/logs.md`：不得记录 token、密钥、简历正文、资料正文、完整问题、完整回答或 JD 全文。

## 前端影响

阶段 2 已新增：

- `frontend-react/src/api/agent.ts`
- `frontend-react/src/pages/agent/AgentWorkspace.tsx`
- 路由 `/agent`

第一版只需要：

- 创建 `pure_read_query`。
- 轮询 `GET /api/agent/tasks/{taskId}`。
- 展示任务状态、工具调用时间线、Observation、最终回答和 evidence 引用。
- 展示但不执行阶段 3-4 的计划审批、CRUD 审批和撤销入口。

阶段 3 扩展：

- 创建 `planning_task`，输入 JD、从用户已上传简历资料读取到的解析摘要和目标；前端不再提供手写简历摘要文本框。
- 展示 `PLAN` 审批卡片，允许 `APPROVED` / `REJECTED` / `CHANGES_REQUESTED`。
- 展示 `OUTPUT` 审批卡片，确认后展示最终 JD/简历适配结果。
- 展示 supported / weak / missing 对齐矩阵、能力缺口和 evidence ID。

阶段 4 扩展：

- 展示 `CRUD` 审批卡片、operationType、resourceType、idempotencyKey 和风险说明。
- `CRUD` 审批通过后展示 `APPLIED_UNDOABLE` 操作和撤销截止时间。
- 撤销窗口内可点击撤销，前端调用 `POST /api/agent/operations/{operationId}/undo`。

阶段 5 扩展：

- 工具能力列表展示 `web_search_probe`。
- 规划类任务可选择联网补充公司背景和技能趋势；未配置 Tavily 时展示可恢复错误，不阻断本地 evidence 对齐。

阶段 6 扩展：

- Agent 工作台的简历模板区域展示“打开图片预览确认页”入口，提示用户先确认可修改区域。
- Agent 工作台不再向任务输入传 `resumeTemplatePath` 或 `resume_template_fill` 工具提示，只保留 `resumeTemplateId` 作为用户选择上下文。
- 历史模板卡支持删除当前用户自己的模板；删除动作调用 Java `DELETE /api/rag/resume/templates/{templateId}`。
- 保存生成的简历版本仍通过 RAG 简历模板补丁、校验和导出接口完成，前端不直接调用 Python 文件接口。

## 阶段 0 验收清单

- `docs/api/agent.md` 覆盖外部接口、内部 Tool Gateway、Python Agent API、状态机、审批、错误码、权限和 Java-Python 契约。
- `infra/sql/init.sql` 包含五张 `agent_*` 表。
- `infra/sql/alter-database/20260621_0200_create_agent_tables.sql` 可用于旧库前向迁移。
- README 中“第二阶段 Agent 能力现状与后续边界”不再声明“未新增接口契约”，并明确 `explicitGrant` 当前只预留。

## 阶段 1 验收清单

- Java 已提供 `POST /api/agent/tasks`、`GET /api/agent/tasks/{taskId}`、`GET /api/agent/tools`。
- Java 已提供 HTTP 级 `POST /api/internal/agent/tools/read`，内部 token 未配置、缺失或错误时返回 `AGENT_INTERNAL_TOKEN_INVALID`。
- 只读 Tool Gateway 从 `taskId` 反查 `agent_task.user_id`，不信任 Python 传入用户范围。
- `rag_query_probe_non_persistent` 和 `retrieval_coverage_probe` 只走 `RagService.queryNonPersistent()`，不写 `rag_query_history`。
- `agent_tool_call.response_json` 只保存脱敏摘要，不保存资料正文、完整回答或 evidence snippet。
- 阶段 1 Java 验证命令：`mvn test`，当前覆盖 34 个测试。

## 阶段 2 验收清单

- Python 已提供 `POST /internal/agent/tasks`，仅在 `X-Agent-Internal-Token` 匹配时启动 `pure_read_query`。
- Python 只读图仅调用 Java `/api/internal/agent/tools/read` 和 Java events 回调，不直连数据库、对象存储或 Python RAG `/internal/*`。
- Java 创建 `pure_read_query` 后在内部 token 已配置时调用 Python Agent；未配置 token 时只保留 `CREATED` 状态。
- Java 已提供 `POST /api/internal/agent/tasks/{taskId}/events`，同样严格校验内部 token，并按事件更新 `agent_task` 和 `agent_tool_call`。
- 前端 `/agent` 可创建只读任务、轮询任务详情、展示工具观察、最终回答和 evidence ID。
- 阶段 2 验证命令：Python `conda run -n learning-evidence-rag python -B -m pytest ai-python/tests/test_agent_api.py -q`；Java Agent 窄测试覆盖 16 个测试。

## 阶段 3 验收清单

- Java 已提供 `POST /api/agent/tasks/{taskId}/reviews/{reviewId}/decide`，只能审批当前用户自己的待处理 review。
- Python `planning_task` 创建后先回写 `PLAN` review，不直接执行变更，也不写业务数据。
- 用户批准 `PLAN` 后，Java 调 Python resume；Python 只通过 Java Read Tool Gateway 获取 RAG evidence，并生成 JD/简历 supported / weak / missing 对齐、能力缺口和风险等级草稿。
- Python 回写 `OUTPUT` review 后任务进入 `WAITING_OUTPUT_REVIEW`；用户批准后任务进入 `COMPLETED`，最终输出保留 evidence ID。
- 前端 `/agent` 支持创建规划类任务、显示计划审批、输出审批、对齐矩阵和缺口建议。

## 阶段 4 验收清单

- Java 已提供内部 `POST /api/internal/agent/tools/mutation/execute`，严格校验内部 token、任务归属、审批状态和幂等键。
- `CRUD` review 通过后才能执行变更工具；未审批、非当前用户、重复幂等冲突都必须拒绝。
- 变更执行前写 `BEFORE` snapshot，执行后写 `AFTER` snapshot，并将 `agent_operation.status` 更新为 `APPLIED_UNDOABLE`。
- 撤销窗口内 `POST /api/agent/operations/{operationId}/undo` 能恢复 before snapshot，并将原操作置为 `UNDONE`。
- 初版变更工具只覆盖 `resume_revision_save`、`jd_learning_plan_save`、`agent_task_cancel_request` 和撤销自身操作，不执行资料重建、普通上传或确定性 RAG 入库。

## 阶段 5 验收清单

- Java 已提供 `web_search_probe` 只读工具，仍通过 `/api/internal/agent/tools/read` 调用并校验内部 token。
- `web_search_probe` 未配置 `TAVILY_API_KEY` 时返回 `AGENT_TAVILY_NOT_CONFIGURED` 且 `retryable=true`。
- Tavily 返回结果只包含 URL、检索时间、摘要、分数和可信度，不写入 RAG evidence 库或查询历史。
- Python 规划类任务在输入 `enableWebSearch=true` 或 `toolHints` 包含 `web_search_probe` 时调用该工具，并在失败时继续本地 evidence 对齐。

## 阶段 6 验收清单

- Agent 工作台不再通过 `resume_template_fill` 自动生成 DOCX，避免 Agent 绕过用户确认决定修改范围。
- 用户可从 Agent 工作台进入 `/resume-template` 图片预览确认页，查看字段区域、勾选是否允许修改、填写改写要求和 evidence 要求。
- 用户可删除自己上传的历史模板；Java 校验归属，数据库级联清理派生记录，服务层清理私有文件。
- 保存生成的简历版本仍走 RAG 简历模板补丁、校验和导出接口，不绕过 Java 权限和模板字段边界。
