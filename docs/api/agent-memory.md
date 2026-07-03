# Agent 记忆接口文档

更新日期：2026-07-03

## 变更摘要

新增 Agent 记忆最小可运行版本接口契约。该契约只覆盖当前用户记忆的查看、创建、确认、拒绝、编辑、归档、删除，以及 Java Tool Gateway 到 Python Memory Service 的候选提炼、冲突判断、索引和检索闭环。

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
