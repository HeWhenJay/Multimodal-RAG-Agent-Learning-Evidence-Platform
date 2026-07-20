# Agent 记忆接口文档

更新日期：2026-07-20

> 迁移状态：本节“纯 Python 对外契约”自 2026-07-20 起为唯一生效契约。下文中 Java Controller、Java Tool Gateway、`/internal/agent/memory/*` 和 Java 调用 Python 索引的描述为历史记录，不能作为当前运行时依赖。

## 纯 Python 对外契约

### 服务边界

- FastAPI 是 `agent_memory_item`、`agent_memory_version`、`agent_memory_audit` 与 `agent_memory_embedding` 的业务权威，直接处理公开 `/api/agent/memories*` 请求。
- 当前用户仅由 Bearer Token 经 `AuthService.current_user()` 得出；请求中的 `userId`、来源任务用户或索引参数均不可信。
- PostgreSQL 是记忆元数据、版本链、审计和检索索引的事实源。无数据库的内存仓储只用于测试或本地演示，不能作为生产持久化后端。
- 统一 Agent 图通过进程内 gateway 调用同一 Python 记忆服务；不存在 Java Gateway、Java 回调、`X-Agent-Internal-Token` 或跨服务索引回调。
- 记忆元数据操作始终由 Python 服务完成。当前 `AgentRuntimeService` 在未接入 embedding 时使用确定性文本降级检索，并保持 `PENDING_INDEX`、`ACTIVE`、`INDEX_FAILED` 状态边界；后续接入 RAG 索引时可复用 Multi-Query、BM25、向量召回与 RRF，不改变公开接口。

### 公开路径

所有接口除 SSE 外均返回 `Result<T>`，并要求 `Authorization: Bearer <token>`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/agent/memories` | 按 `status`、`memoryType`、`namespace`、`scopeType` 查询当前用户记忆 |
| `POST` | `/api/agent/memories` | 创建当前用户显式授权记忆 |
| `GET` | `/api/agent/memories/{memoryId}` | 查询当前用户单条记忆及可见版本/审计摘要 |
| `PATCH` | `/api/agent/memories/{memoryId}` | 修改正文或收窄作用域，生成新版本 |
| `POST` | `/api/agent/memories/{memoryId}/confirm` | 确认待审记忆并进入索引 |
| `POST` | `/api/agent/memories/{memoryId}/reject` | 拒绝待审记忆 |
| `POST` | `/api/agent/memories/{memoryId}/archive` | 归档并停用检索索引 |
| `DELETE` | `/api/agent/memories/{memoryId}` | 删除正文、停用索引并保留审计链 |

### 创建、状态与检索

创建请求：

```json
{
  "memoryType": "PREFERENCE",
  "namespace": "resume_style",
  "scopeType": "USER",
  "subjectKey": "preferred_resume_tone",
  "content": "以后简历项目描述优先强调可追溯 evidence。",
  "summary": "简历描述偏好：强调可追溯 evidence。",
  "importance": 0.82
}
```

显式创建先写入 `PENDING_INDEX`，Python 索引成功后转为 `ACTIVE`，失败时转为 `INDEX_FAILED`。候选记忆由 Agent 产生时先写 `PENDING_REVIEW`，只有当前用户调用 `confirm` 后才可检索。默认列表不返回 `DELETED`；默认注入 Agent 上下文必须同时满足 `ACTIVE`、未删除、未过期且非 `HIGH` 敏感级别。

### 所有权、版本与错误

- 查询、确认、拒绝、归档、修改和删除均强制 `memory.user_id == currentUserId`；不属于当前用户时返回 `AGENT_MEMORY_NOT_FOUND`，不暴露资源存在性。
- `PATCH` 只能收窄作用域：`USER -> PROJECT -> MATERIAL -> TASK / SESSION`，禁止扩大为更宽范围；正文变化生成新版本，旧版本标记为 `SUPERSEDED`。
- 删除会将 `content` 与 `summary` 替换为不可逆删除标记，并写入 `agent_memory_audit`；后续检索不会返回该记忆。
- 常见业务错误使用中文 `Result.code=0`：`AGENT_MEMORY_VALIDATION_FAILED`、`AGENT_MEMORY_SCOPE_ESCALATION`、`AGENT_MEMORY_REVIEW_REQUIRED`、`AGENT_MEMORY_DELETED`、`AGENT_MEMORY_INDEX_FAILED`。

### 前端影响

React 继续调用现有 `/api/agent/memories*` 路径与 `Result<T>` 契约。前端不传、也不依赖 `userId`；切换 Vite 代理到 FastAPI 后，记忆 CRUD 与任务中的记忆检索均不再依赖 Java 7080。

### 当前实现落点

- 公开路径统一由 `ai-python/app/api/agent.py` 提供；`app/api/agent_memory.py` 仅保留空导入兼容模块，不再暴露内部令牌接口。
- `ai-python/app/agent_runtime/service.py` 负责所有权、版本链、作用域收窄、状态流转和正文擦除；`ai-python/app/agent_runtime/repository.py` 提供 PostgreSQL 与测试内存仓储。
- 图内的记忆读取通过 `LocalAgentGateway` 直接调用当前用户的 `memory_context`，候选记忆保持 `PENDING_REVIEW`，不会被自动写入或激活。

## 当前实现变更摘要

Agent 记忆的查看、创建、确认、拒绝、编辑、归档和删除均由 Python FastAPI 直接处理。统一图通过进程内 `LocalAgentGateway` 调用记忆服务；PostgreSQL 保存元数据、版本链、审计和索引状态，未配置 embedding 时使用确定性文本检索降级。

## 历史迁移记录（仅供追溯，不是运行依赖）

以下章节保留迁移前 Java Controller、Tool Gateway 和 Python Memory Service 的拆分契约，仅用于理解旧数据或回滚差异；当前启动和联调不得使用其中的内部 URL、令牌或回调。

2026-07-03 更新：PostgreSQL 后端的记忆索引写入改为复用 RAG 主链路 `embed_text` 生成真实 1024 维语义向量；记忆查询改为 BM25 与 PostgreSQL/pgvector `<=>` 向量召回共同参与 RRF 融合，并对只由向量召回命中的记忆回填元数据后再返回。

核心边界：

- React 只调用 Java `/api/agent/memories*` 和 `/api/agent/tasks*`。
- Java 是当前用户、权限、状态、版本、审计、幂等和统一响应边界。
- Python Memory Service 只负责候选提炼、冲突判断、检索评分和 `agent_memory_embedding` 索引，不修改 `agent_memory_item` 状态。
- Python Agent 只能通过 Java Tool Gateway 使用 `agent_memory_retriever`、`agent_memory_candidate_proposer`、`agent_memory_candidate_save`。
- `PENDING_REVIEW` 只展示给用户确认，不进入默认 `memoryContext`。

## 记忆状态

| 状态 | 含义 | 默认可检索 |
| --- | --- | --- |
| `PENDING_REVIEW` | Agent 或 Python 推断出的待确认候选 | 否 |
| `PENDING_INDEX` | 用户确认或显式“记住”后等待 Python 索引 | 否 |
| `ACTIVE` | 索引成功且可默认召回 | 是 |
| `INDEX_FAILED` | 元数据已保存但索引失败 | 否 |
| `ARCHIVED` | 用户归档，保留正文和版本链 | 否 |
| `SUPERSEDED` | 被新版本替代 | 否 |
| `REJECTED` | 用户拒绝候选 | 否 |
| `DELETED` | 用户删除，正文已擦除或软删除 | 否 |

默认检索必须同时满足：

```text
status = ACTIVE
deleted_at IS NULL
valid_until IS NULL OR valid_until > now()
sensitivity_level != HIGH
```

## 记忆作用域

`scopeType` 可取 `USER` / `PROJECT` / `MATERIAL` / `TASK` / `SESSION` / `SYSTEM`。普通用户不能创建或扩大到 `SYSTEM`。`PATCH` 只能收窄作用域：

```text
USER -> PROJECT -> MATERIAL -> TASK / SESSION
```

禁止把 `TASK`、`SESSION`、`MATERIAL` 放大为 `PROJECT` 或 `USER`。`provenance` 字段只读，包括 `sourceTaskId`、`sourceToolCallId`、`sourceReviewId`、`sourceHash`、`evidenceRefs`、`consentSource`。

## Java 对外接口

所有外部接口返回 `Result<T>`。

### 创建显式记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/memories` |
| 鉴权 | Bearer Token |
| 响应 | `Result<AgentMemoryVO>` |

请求示例：

```json
{
  "memoryType": "PREFERENCE",
  "namespace": "resume_style",
  "scopeType": "USER",
  "subjectKey": "preferred_resume_tone",
  "content": "以后简历项目描述优先强调可追溯 evidence，不要写空泛自评。",
  "summary": "简历项目描述偏好：强调可追溯 evidence，避免空泛自评。",
  "importance": 0.82
}
```

规则：

- 创建接口表示用户显式“记住”，Java 写入 `PENDING_INDEX` 后调用 Python 索引。
- 索引成功后状态为 `ACTIVE`；索引失败时状态为 `INDEX_FAILED`。
- 命中密钥、token、签名 URL、身份证、手机号等敏感模式时返回 `AGENT_MEMORY_SENSITIVE_REJECTED`。

### 查询当前用户记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/agent/memories` |
| 查询参数 | `status`、`memoryType`、`namespace`、`scopeType` |
| 响应 | `Result<List<AgentMemoryVO>>` |

默认不返回 `DELETED`。如需查看某状态，显式传 `status`。

### 查询单条记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/agent/memories/{memoryId}` |
| 响应 | `Result<AgentMemoryDetailVO>` |

详情包含当前记忆、版本关系和脱敏审计记录。

### 确认待审记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/memories/{memoryId}/confirm` |
| 响应 | `Result<AgentMemoryVO>` |

规则：

- 仅当前用户自己的 `PENDING_REVIEW` 或 `INDEX_FAILED` 可确认。
- Java 将状态置为 `PENDING_INDEX`，调用 Python `/internal/agent/memory/index/upsert`。
- Python 成功后 Java 置为 `ACTIVE`；失败则置为 `INDEX_FAILED`。
- 确认来源写为 `USER_REVIEW`，审计 action 写 `CONFIRM` 和 `INDEX_UPSERT`。

### 拒绝待审记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/memories/{memoryId}/reject` |
| 响应 | `Result<AgentMemoryVO>` |

拒绝后状态为 `REJECTED`，Java 同步通知 Python 删除或停用索引；后续默认检索不召回。

### 修改记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `PATCH` |
| 路径 | `/api/agent/memories/{memoryId}` |
| 响应 | `Result<AgentMemoryVO>` |

允许字段：

```json
{
  "content": "新的中文记忆内容",
  "summary": "新的短摘要",
  "namespace": "resume_style",
  "subjectKey": "preferred_resume_tone",
  "scopeType": "PROJECT",
  "scopeId": "rag-agent-platform"
}
```

规则：

- `content` 或 `summary` 修改必须生成新 `agent_memory_item`，旧记忆置为 `SUPERSEDED`。
- 修改后写 `agent_memory_version`，`relationType=REFINES`，并重建索引。
- `scopeType/scopeId` 只能收窄，不能放大。
- provenance、置信度、来源引用、用户 ID、删除时间和访问计数字段不可由前端修改。

### 归档记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/agent/memories/{memoryId}/archive` |
| 响应 | `Result<AgentMemoryVO>` |

归档后状态为 `ARCHIVED`，保留正文和版本链，Java 同步删除或停用 embedding。

### 删除记忆

| 项目 | 内容 |
| --- | --- |
| 方法 | `DELETE` |
| 路径 | `/api/agent/memories/{memoryId}` |
| 响应 | `Result<AgentMemoryVO>` |

删除规则：

- Java 校验当前用户后把状态置为 `DELETED`，写 `deletedAt`。
- `content` 和 `summary` 替换为不可逆删除标记，只保留哈希、版本关系和审计摘要。
- Java 同步调用 Python `/internal/agent/memory/index/delete`。
- 版本链和审计记录不得展示已删除正文。

## Java 内部 Tool Gateway

### `agent_memory_retriever`

| 类型 | 用途 |
| --- | --- |
| READ | 按当前任务目标检索可注入 `memoryContext` 的记忆 |

请求参数：

```json
{
  "query": "分析后端实习 JD 适配度",
  "topK": 5,
  "namespaces": ["career_profile", "resume_style"],
  "memoryTypes": ["PREFERENCE", "SEMANTIC"]
}
```

Java 行为：

- 从 `taskId` 查询 `agent_task.user_id`，忽略 Python 传入的 `userId`。
- 构造授权过滤：当前用户、当前任务 scope、默认 `ACTIVE`。
- 调 Python `/internal/agent/memory/query`。
- 对 Python 返回的 `memoryId/userId/status/scope/deletedAt` 做二次校验。
- 更新 `access_count/last_accessed_at`，返回短摘要，不返回完整任务历史。

### `agent_memory_candidate_proposer`

| 类型 | 用途 |
| --- | --- |
| READ | 从任务输入、草稿、最终输出和工具摘要中生成待确认候选，不落库 |

Java 调 Python `/internal/agent/memory/extract`，Python 返回候选、冲突关系和敏感性判断。候选默认只能由 Java 保存为 `PENDING_REVIEW`。

### `agent_memory_candidate_save`

| 类型 | 用途 |
| --- | --- |
| MUTATION | 经 CRUD 审批或显式“记住”授权后保存候选 |

规则：

- 没有显式授权时只保存为 `PENDING_REVIEW`。
- 显式“记住”或候选页确认后进入 `PENDING_INDEX -> ACTIVE`。
- 该工具仍由 Java 校验 task owner、审批记录和幂等键。

## Python 内部接口

内部接口必须校验 `X-Agent-Internal-Token`，令牌解析策略与 Agent 主接口一致：优先使用 `EVIDENCE_AGENT_INTERNAL_TOKEN`，本地未配置时读取或创建仓库根目录 `.local/agent-internal-token`，也可通过 `EVIDENCE_AGENT_INTERNAL_TOKEN_FILE` 指定共享密钥文件。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/agent/memory/query` | 在 Java 授权范围内执行记忆检索 |
| `POST` | `/internal/agent/memory/extract` | 从任务快照提炼记忆候选 |
| `POST` | `/internal/agent/memory/conflicts` | 判断新旧记忆关系 |
| `POST` | `/internal/agent/memory/index/upsert` | 为 Java 已确认记忆写入或更新 embedding |
| `POST` | `/internal/agent/memory/index/delete` | 删除或停用指定 memoryId 的 embedding |

Python 约束：

- 只能处理 Java 传入的 `memoryId/userId/scope`。
- 只读写 `agent_memory_embedding`，查询时可 join `agent_memory_item` 做状态过滤。
- 不读取或修改 `agent_task`、`learning_material`、`resume_template`、`rag_query_history` 等业务表。
- 记忆检索使用确定性 Multi-Query、BM25、embedding 向量召回、RRF 和时近性/重要性/置信度加权。
- 无 `RAG_DATABASE_URL` 或 `DATABASE_URL` 时，Python Memory Service 使用内存索引和 hash embedding，主要用于离线单测与本地演示。
- 配置 PostgreSQL 后端时，写入侧复用 RAG 主链路 `embed_text` 生成真实语义向量并写入 `agent_memory_embedding.embedding`；查询侧使用 `embedding.embedding <=> %s::vector` 执行 pgvector 向量召回。
- 查询响应的 `diagnostics` 至少应能标识 `retrievalProvider`、`embeddingProvider`、`pgvectorUsed`、`bm25CandidateCount`、`vectorHitCount`、`finalCandidateCount`、`rankedListCount`、`expandedQueries` 和 `vectorDimensions`，用于确认当前是否命中 PostgreSQL/pgvector 路径。

## 错误码

| 错误码 | HTTP 建议 | 含义 |
| --- | --- | --- |
| `AGENT_MEMORY_NOT_FOUND` | 404 | 记忆不存在或不属于当前用户 |
| `AGENT_MEMORY_FORBIDDEN` | 403 | 当前任务无权访问该记忆或 scope |
| `AGENT_MEMORY_VALIDATION_FAILED` | 400 | 内容、类型、scope 或状态非法 |
| `AGENT_MEMORY_SCOPE_ESCALATION` | 409 | PATCH 尝试放大 scope |
| `AGENT_MEMORY_SENSITIVE_REJECTED` | 400 | 命中敏感内容，拒绝写入 |
| `AGENT_MEMORY_REVIEW_REQUIRED` | 409 | 记忆需要用户确认后才能激活 |
| `AGENT_MEMORY_INDEX_FAILED` | 502 | 元数据已保存，但 embedding 索引失败 |
| `AGENT_MEMORY_DELETED` | 410 | 记忆已删除，不可检索或修改 |
| `AGENT_MEMORY_PYTHON_UNAVAILABLE` | 502 | Python Memory Service 不可用 |

## 前端影响

`/agent` 页面新增轻量记忆区：

- 展示本次任务使用的 `memoryContext`。
- 展示当前用户 `PENDING_REVIEW` 记忆候选。
- 支持确认、拒绝、归档和删除。
- 页面文案区分“RAG evidence”和“Agent 记忆”：前者是资料证据，后者只作为个性化上下文。
