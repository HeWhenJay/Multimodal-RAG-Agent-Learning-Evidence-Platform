# RAG 接口文档

更新日期：2026-07-04

## 变更摘要

本次补齐“多格式文档解析到 RAG 入库”接口契约，并接入登录用户隔离、1024 维百炼 embedding、视频字幕/OCR 提取修复、视频画面 OCR 近重复治理、RAG 进度事件、长视频分片上传后台收尾、查询链路进度返回、RAG 询问历史筛选、前端 Markdown 回答渲染、资料 Markdown 预览页、视频 evidence 内部播放器定位，以及 Stage 1 父子索引、summary child、OCR occurrence、父段聚合诊断和 `STRICT_EVIDENCE_GUARD_V1` 回答准入。2026-07-04 新增 Kafka 驱动高吞吐索引流水线契约：Java 通过事务内资料记录、索引任务和 Outbox 事件发布索引请求，Python Kafka worker 只写 staging 索引，Java 校验 active job 与 requestVersion 后再发布 promote 请求，Python 完成 staging 到 canonical 的提升。第一阶段将上传/重建触发后的 RAG 索引请求、进度、结果、promote 与终态同步接入 Kafka；长视频分片收尾仍本机 `@Async`，合并后才进入 Kafka 索引；低延迟 `/api/rag/query` 默认仍走 HTTP，不改变 evidence 结构。

边界约定：

- React 只调用 Java Spring Boot。
- Java 负责资料记录、文件上传、阿里 OSS 对象存储、原始文件路径、解析状态、登录用户边界、统一 `Result<T>` 响应和调用 Python。
- Python FastAPI 负责多格式解析路由、原始视频处理、MinerU/OCR 降级、`DocumentBlock` 统一模型、解析质量评分、递归切块、BM25、百炼 `text-embedding-v4` 1024 维向量索引、RRF/RAG-Fusion、百炼 rerank、百炼 LLM 回答生成和 evidence 引用。
- RAG 查询回答准入由 Python `query.guard` 统一判定。Java 只透传 `answerStatus/refusalReason/refusalPolicy/confidence/supportingEvidenceIds/refusalMessage`，并在历史和日志中保存结构化结果；前端只展示状态，不自行判断是否可回答。
- 数据库初始化脚本位于 `infra/sql/init.sql`，增量迁移位于 `infra/sql/alter-database/`。
- RAG 进度事件复用 `log_event` 表，`event_type=rag_progress`，不新增独立进度表；Python 方法级控制面板日志使用 `event_type=rag_process`，错误仍写入 `log_error`。
- RAG 询问历史由 Java 写入 `rag_query_history`，只保存业务查询快照、问题、回答、证据 JSON 和状态；Python 查询任务仍是进程内短期进度快照，不承担长期历史存储。
- Kafka 默认关闭。关闭时 Java 保留现有 `@Async + HTTP` 后台索引链路；开启后才使用 `rag_index_job`、`rag_outbox_event`、`rag_consumed_event`、Kafka request/progress/result/promote topics 和 Python Kafka worker。
- Git 提交时同步维护 `infra/sql/init.sql` 和 `infra/sql/alter-database/` 下对应增量迁移。

鉴权约定：

- 前端登录后保存 Bearer Token，并在 RAG 和页面数据请求中自动携带 `Authorization: Bearer <token>`。
- Java 从 token 解析当前用户 ID，并写入 `learning_material.user_id`、Python 索引请求 `userId` 和查询请求 `metadataFilter.userId`。
- 前端传入的 `metadataFilter.userId` 会被 Java 覆盖为当前登录用户，不能越权查询其他用户资料。

## 状态机

| 状态 | 含义 | 前端展示建议 |
| --- | --- | --- |
| `PENDING` | Java 已创建资料记录，等待调用 Python 解析 | 等待解析 |
| `PARSING` | Python 正在解析、切块和索引 | 解析中 |
| `READY` | 解析和索引完成，可检索 | 已入库 |
| `PARTIAL` | 主解析成功但存在补充解析失败、部分空内容或低置信块，仍可检索已入库证据 | 部分完成 |
| `FAILED` | 无可用文本或索引失败 | 解析失败 |
| `REINDEXING` | 资料重新解析/重新索引中 | 重建索引 |

`PARTIAL` 不是接口失败。Java 应保存该状态并返回资料摘要、切块数和可检索 evidence；前端应提示“部分完成”，允许用户继续检索。

## Kafka 高吞吐索引流水线

Kafka 仅用于上传/重建触发后的高吞吐索引、进度、结果、promote 与索引终态同步，不改变查询默认路径。开启条件为 Java `evidence.rag.kafka.enabled=true` 且 Python `RAG_KAFKA_ENABLED=true`；任一侧未开启时，旧 HTTP 索引链路必须保持可用。

### 核心不变量

- Java 在同一事务内写入 `learning_material`、`rag_index_job` 和 `rag_outbox_event`，上传接口或重建接口快速返回。
- Python index worker 永远只写 `stagingDocumentId`，例如 `material-88__job-job_20260704_001`，`visibility_scope='staging'`。
- Java 只有在 `learning_material.active_index_job_id` 等于 result 的 `jobId`，且 `learning_material.index_request_version` 等于 result 的 `requestVersion` 时，才发布 `RAG_PROMOTE_REQUESTED`。
- Python Promote Worker 执行 promote 前必须再次调用 Java active-check 内部接口校验 `materialId/jobId/requestVersion` 仍为 active job；校验失败不得调用 pgvector promote。
- `PgVectorRagStore.promote_staged_index(...)` 还需做存储层防御：如果 canonical 已存在且其 metadata 中 `requestVersion` 大于本次请求版本，必须拒绝覆盖。
- stale job、过期 retry、取消 job 即使完成，也只能保留 staging，不得覆盖 canonical。
- 查询仍由 Java 强制 `metadataFilter.userId=currentUserId` 和 `visibilityScope='private'`，staging 不得被 private 查询命中。
- Java 不直接操作 Python RAG 表，staging promote 只能由 Python 执行。

### Kafka topics

| Topic | 生产者 | 消费者组 | Key | 用途 |
| --- | --- | --- | --- | --- |
| `rag.material.index.request.v1` | Java Outbox | `rag-python-index-workers` | `canonicalDocumentId` | 触发 Python 索引 staging |
| `rag.upload.finalize.request.v1` | 预留，默认不由 Java Outbox 发布 | `rag-java-upload-finalizers` | `uploadId` | 未来共享 chunkRoot 或 host affinity 模式下触发 Java 分片合并；第一阶段当前默认不发布 |
| `rag.material.index.progress.v1` | Python | `rag-java-progress-writers` | `canonicalDocumentId` | 写入用户可见进度 |
| `rag.material.index.result.v1` | Python | `rag-java-result-writers` | `canonicalDocumentId` | Java 校验 active job 后发布 promote |
| `rag.material.index.promote.request.v1` | Java | `rag-python-promote-workers` | `canonicalDocumentId` | 触发 staging 提升为 canonical |
| `rag.material.index.promote.result.v1` | Python | `rag-java-promote-result-writers` | `canonicalDocumentId` | Java 更新资料和 job 终态 |
| `rag.material.index.retry.1m.v1` / `rag.material.index.retry.10m.v1` / `rag.material.index.retry.1h.v1` | Python | Python retry scheduler | `canonicalDocumentId` | 按 `notBefore` 延迟重投 request |
| `rag.material.index.dlq.v1` | Python | `rag-java-dlq-writers` | `canonicalDocumentId` | 记录不可恢复索引失败 |
| `rag.upload.finalize.dlq.v1` | Java | `rag-java-dlq-writers` | `uploadId` | 记录分片收尾失败 |

Kafka 没有原生延迟消息。retry scheduler 消费 retry topic 后，如果 `notBefore` 未到，应暂停对应 partition 并等待，不提交 offset；到期后发回 request topic，发送成功才提交原 offset。

### Envelope

所有 Kafka 消息使用统一 JSON envelope：

```json
{
  "schemaVersion": "1.0",
  "messageId": "uuid",
  "originalMessageId": "uuid",
  "messageType": "RAG_INDEX_REQUESTED",
  "eventTime": "2026-07-04T10:00:00Z",
  "producer": "backend-java",
  "traceId": "trace-id",
  "correlationId": "material-88",
  "partitionKey": "material-88",
  "idempotencyKey": "RAG_INDEX:material-88:job_20260704_001:v1",
  "attempt": 0,
  "notBefore": null,
  "payload": {}
}
```

幂等键按消息类型独立生成：

| 消息类型 | 幂等键 |
| --- | --- |
| index request/retry | `RAG_INDEX:{canonicalDocumentId}:{jobId}:v1` |
| progress | `RAG_PROGRESS:{canonicalDocumentId}:{jobId}:{progressSequence}:v1` |
| index result | `RAG_INDEX_RESULT:{canonicalDocumentId}:{jobId}:v1` |
| promote request | `RAG_PROMOTE:{canonicalDocumentId}:{jobId}:v1` |
| promote result | `RAG_PROMOTE_RESULT:{canonicalDocumentId}:{jobId}:v1` |
| DLQ | `RAG_DLQ:{canonicalDocumentId}:{jobId}:{attempt}:v1` |
| upload finalize request | `RAG_UPLOAD_FINALIZE:{uploadId}:{materialId}:v1` |

`RAG_INDEX_REQUESTED.payload` 至少包含：

- `jobId`、`operation`，其中 operation 为 `INDEX_UPLOAD`、`REINDEX` 或 `INDEX_TEXT`。
- `materialId`、`canonicalDocumentId`、`stagingDocumentId`、`userId`、`title`、`documentType`、`source`。
- `visibilityScope='private'`、`stagingVisibilityScope='staging'`、`highPrecision`、`requestVersion`。
- `sourceRef`，支持 `INLINE_TEXT` 和 `JAVA_SOURCE_API`。`JAVA_SOURCE_API` 包含 `javaBaseUrl`、`downloadPath`、`filename`、`contentType`、`storageType`、`sourcePath`、`objectKey`、`publicUrl`。
- `text`，仅 `INLINE_TEXT` 使用；日志、DLQ 和错误上下文不得记录正文。

`RAG_INDEX_PROGRESS.payload` 对齐现有 `ProgressEvent`，并额外携带 `jobId`、`materialId`、`canonicalDocumentId`、`stagingDocumentId`、`userId`、`parser`、`requestVersion` 和 `progressSequence`。

`RAG_INDEX_RESULT.payload` 对齐现有 `IndexResponse`，并额外携带 `jobId`、`materialId`、`canonicalDocumentId`、`stagingDocumentId`、`requestVersion`、`errorCode` 和 `errorMessage`。

`RAG_PROMOTE_REQUESTED.payload` 携带 `jobId`、`materialId`、`canonicalDocumentId`、`stagingDocumentId`、`requestVersion` 和 `chunkCount`。`RAG_PROMOTE_RESULT.payload` 携带 `alreadyPromoted`、`canonicalChunkCount`、`stagingChunkCount`、`status` 和错误字段。

### Outbox 与消费幂等

- `rag_outbox_event(topic,idempotency_key)` 唯一，发布器多实例抢占 `NEW/FAILED` 到期事件，状态改为 `PUBLISHING` 并设置 `lease_until` 和 `locked_by`，成功后标记 `PUBLISHED`，失败后标记 `FAILED` 并设置 `next_attempt_at`。
- `rag_consumed_event(consumer_name,message_id)` 与 `rag_consumed_event(consumer_name,message_type,idempotency_key)` 唯一，保证每个消费者幂等。progress 的幂等键包含 `progressSequence`，100 条不同进度不得被去重成 1 条。
- Python worker manual commit：staging 写入成功且 result/retry/DLQ 发送 ack/flush 成功后，才提交原 offset。result/retry/DLQ 发送失败时不提交，等待 Kafka redelivery。
- DLQ payload 和日志上下文只能保存资料 ID、文件名、错误码、错误摘要、attempt、topic、jobId 和 requestVersion，不得包含文档正文、简历全文、API key 或 OSS secret。

### Java 内部 Source API

Kafka worker 需要从 Java 读取原始资料时使用内部接口：

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/internal/rag/materials/{materialId}/source` |
| Header | `X-RAG-Internal-Token: <token>` |
| Query | `jobId`、`requestVersion` |
| 响应 | 原始资料字节流，保留 `Content-Type` 和 `Content-Disposition` |

Java 必须校验内部 token、资料存在、`active_index_job_id` 匹配、`index_request_version` 匹配、job 未取消且未终态。失败响应使用中文错误：`401` 表示缺少或错误 token，`403` 表示 job 与资料当前 active job 不匹配，`404` 表示资料不存在，`410` 表示 job 已过期或被新版本取代。
Java Source API 使用服务端流式响应，Python worker 使用 `httpx.stream()` 写入临时文件；文档类解析在现有 parser 需要 bytes 时才读取临时文件，视频类解析优先使用临时文件路径。临时文件必须在解析完成或失败后清理。

### Java 内部 active-check API

Promote Worker 在真正改写 canonical 前必须重新向 Java 校验 active job：

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/internal/rag/materials/{materialId}/index-jobs/{jobId}/active` |
| Header | `X-RAG-Internal-Token: <token>` |
| Query | `requestVersion` |
| 成功响应 | `Result<Map>`，`data.active=true`、`materialId`、`jobId`、`requestVersion` |

失败语义与 Source API 一致：`403/410` 表示 stale 或过期，Python 必须发送 `RAG_PROMOTE_RESULT.status=FAILED` 且 `errorCode=RAG_PROMOTE_STALE`，同时不得调用 `promote_staged_index(...)`。

### Python staging promote

Python index worker 调用现有解析、递归切块、摘要、embedding 后，以 `stagingDocumentId` 调 `PgVectorRagStore.index_blocks()`，metadata 写入 `canonicalDocumentId`、`jobId`、`requestVersion`、`stagingDocumentId`、`sourceJobId`，`visibilityScope='staging'`。

`PgVectorRagStore.promote_staged_index(...)` 需要幂等：

1. 如果 canonical 已存在，并且 metadata 中的 `sourceJobId/requestVersion/stagingDocumentId` 与本次请求一致，且 canonical chunk 数等于 staging/result chunkCount，直接返回 `alreadyPromoted=true`。
2. 如果 canonical 已存在且 metadata 中 `requestVersion` 大于本次请求版本，直接拒绝，避免旧 promote 覆盖新 canonical。
3. 正常 promote 在同一事务内删除 canonical，插入 canonical document，把 staging chunks 复制为 canonical chunks，`chunk_id` 从 staging 前缀替换为 canonical 前缀，metadata 中 `documentId` 改为 canonical，同时保留 `sourceJobId/requestVersion/stagingDocumentId`。
4. promote 后 staging 可标记为 `staging_promoted`；Java 确认 promote result、job 进入 `SUCCEEDED` 前，不物理删除 staging。
5. 清理任务只清理 `SUCCEEDED` 且超过 `RAG_STAGING_RETENTION_HOURS`（默认 24 小时）的 staging；`FAILED/DLQ` staging 默认保留 7 天。

### 分片收尾部署限制

第一阶段 Kafka 不承载本地分片目录路径。长视频分片合并、对象存储上传仍固定由接收最后一个分片的 Java 实例通过本地 `@Async` 完成；合并完成后，如果 Kafka 已开启，仅后续 RAG 索引请求进入 Kafka。这样避免多 Java 实例部署时 `directory/chunkRoot` 指向另一台机器导致收尾失败。保留 `rag.upload.finalize.request.v1` 与 DLQ consumer 只用于未来共享分片存储或 host affinity 模式，当前默认不发布该 topic。

## RAG 进度事件

RAG 进度事件按现有代码链路拆分，不使用临时示例阶段名。前端展示时优先读取 `LearningMaterialVO.latestProgress`，需要历史时读取 `progressEvents`。
视频资料会在资料卡片中额外汇总展示 `parse.video.frame.extract`、`parse.video.frame.candidates`、`parse.video.slide_detect` 和 `parse.video.ocr`，用户可以看到抽帧候选数量、PPT 翻页命中、视觉去重跳过、最小间隔和最终进入 OCR 的帧数。Java 返回资料进度时除最近事件外会额外保留这些视频关键阶段，避免长视频逐帧 OCR 进度把翻页检测结果挤出前端。

日志事件职责：

| 事件类型 | 表 | 来源 | 用途 |
| --- | --- | --- | --- |
| `rag_progress` | `log_event` | Python/Java | 用户可见资料进度，展示当前 RAG 阶段、当前 chunk、总 chunk 和百分比 |
| `rag_process` | `log_event` | Python | 控制面板处理日志，记录接口入口、文件读取、解析方法、清洗、切块、摘要、每个 chunk 的 embedding/入库、检索、rerank 和回答生成的方法级开始/完成/失败 |
| 错误日志 | `log_error` | Java/Python | 失败聚合、错误码、堆栈、Python endpoint、响应体摘要和阶段定位 |

当 Python 调用在任意阶段真实失败时，Python 或 Java 会补写一条 `index.failed` 的 `rag_progress` 终态事件，并将资料状态标为 `FAILED`，避免前端刷新后仍停留在旧的运行中阶段。
长视频、大文件索引可能超过 Java 等待 Python HTTP 响应的 `index-timeout-seconds`；该超时只表示 Java 等待最终响应超时，不代表 Python 索引失败。此时 Java 保持资料为 `PARSING/REINDEXING`，继续接收 Python 的实时 `rag_progress` 回调。Python 最终上报 `index.completed` 时，回调上下文必须携带 `parseStatus/chunkCount/parser`，Java 根据该终态进度同步 `learning_material.status/parser/chunk_count`；Python 最终上报 `index.failed` 时，Java 才将资料状态标为 `FAILED`。

索引链路阶段：

| 阶段码 | 含义 | 典型展示 |
| --- | --- | --- |
| `index.request` | Python 已接收索引请求 | 已接收索引任务 |
| `parse.route` | 根据文件名、documentType 和 contentType 选择解析路线 | 已识别资料类型 |
| `parse.pdf` | PDF 解析，优先 MinerU，失败后 native/OCR 降级 | 正在解析 PDF |
| `parse.docx` | Word 原生解析，必要时 LibreOffice PDF 补充 | 正在解析 Word |
| `parse.pptx` | PPT 原生解析；`python-pptx` 缺失时使用 PPTX XML 降级解析，必要时 LibreOffice PDF 补充 | 正在解析 PPT |
| `parse.spreadsheet` | openpyxl/pandas 解析表格资料 | 正在解析表格 |
| `parse.image.ocr` | 图片 OCR 解析 | 正在进行图片 OCR |
| `parse.video` | 视频处理入口，包含 ASR、关键帧、OCR 和片段摘要 | 正在处理视频 |
| `parse.video.asr` | 视频 ASR 子阶段，包含 filetrans 提交、轮询、重试、降级和同步 ASR | 正在转写视频音频 |
| `parse.video.frame.extract` | 按全视频时间轴抽取候选帧 | 正在抽取视频候选帧 |
| `parse.video.frame.candidates` | 候选帧抽取完成，准备做 PPT 翻页检测和视觉去重 | 已抽取候选帧 |
| `parse.video.slide_detect` | 计算画面差异、检测 PPT 翻页、视觉去重，并按可选 OCR 上限选择关键帧 | 正在检测 PPT 翻页 |
| `parse.video.ocr` | 对筛选后的关键帧逐帧执行 OCR | 正在识别关键帧 OCR |
| `parse.text` | Markdown、字幕或普通文本结构解析 | 正在解析文本 |
| `parse.completed` | 解析完成，已得到 DocumentBlock 和解析质量 | 解析完成 |
| `sanitize.blocks` | 入库前清洗正文、元数据和 PostgreSQL 不支持字符 | 正在清洗文本 |
| `chunk.recursive` | 执行递归切块 | 当前文件被切分为 xx 块 |
| `summary.index` | 生成文档摘要和章节摘要 | 正在生成摘要索引 |
| `embedding.chunk` | 对当前 chunk 生成 embedding | 第 x/y 块：生成 embedding |
| `vector.upsert.chunk` | 当前 chunk 写入 BM25 词频、metadata 和 pgvector | 第 x/y 块：写入向量数据库 |
| `memory.upsert.chunk` | 本地内存检索兜底模式写入 chunk、BM25 词频和 embedding | 第 x/y 块：写入内存检索索引 |
| `index.completed` | 文档、摘要、切块和向量索引写入完成 | 索引完成 |
| `index.failed` | 索引过程失败 | 索引失败 |

查询链路阶段：

| 阶段码 | 含义 |
| --- | --- |
| `query.expand` | Multi-Query 生成查询变体；生产环境优先由 LLM 根据用户问题生成多角度检索问法，失败或未配置 Key 时退回本地确定性改写 |
| `query.filter` | 根据 userId、visibilityScope、documentType 等过滤候选块 |
| `query.bm25` | BM25 关键词召回 |
| `query.vector` | 向量召回 |
| `query.fusion` | RRF/RAG-Fusion 排序融合 |
| `query.rerank` | rerank 重排 |
| `query.guard` | 回答准入判定；根据候选数量、过滤结果、重排分数、关键词覆盖、snippet 质量和风险信号决定 `ANSWERED/REFUSED` |
| `query.answer` | 生成带 evidence 引用的回答 |

查询阶段支持两种调用方式：

- `/api/rag/query`：同步问答接口，`progressEvents` 随最终响应一次性返回，适合简单调用或测试。
- `/api/rag/query/tasks` + `/api/rag/query/tasks/{taskId}`：前端推荐方式。Java 代理 Python 查询任务接口，Python 在执行 `store.query()` 时通过 `RagProgressReporter.on_emit` 实时写入任务快照，前端轮询任务详情读取运行状态、当前阶段和已有阶段事件。这样用户能看到检索链路正在推进，而不是只看到第一步后等待最终响应。

查询阶段的 `progressEvents` 不写入资料级 `log_event` 进度表。每个阶段需要返回可读详情：`query.expand` 说明 Multi-Query 的生成提供方、模型、是否降级以及改写出的所有查询；`query.filter` 说明过滤条件和候选切块数；`query.bm25/query.vector` 说明每个查询变体的召回数量和 Top evidence；`query.fusion` 说明参与融合的列表数、候选预算和融合后候选；`query.rerank` 说明重排模型、输入/输出数量和 Top evidence；`query.guard` 说明 `answerStatus/refusalReason/confidence/supportingEvidenceCount/candidateCount/thresholds`；`query.answer` 说明使用的回答模型、最终 evidence 数量和生成状态。若 `query.guard` 判定 `REFUSED`，`query.answer` 仍返回 `COMPLETED` 事件，detail 为“证据不足，已跳过 LLM 回答生成”，但 Python 不调用回答模型。前端不得只显示阶段最后一条事件，BM25 和向量召回需要保留每个查询变体对应的事件详情。

Multi-Query 生成契约：

- Python 查询链路负责生成扩展查询，Java 只透传请求和保存 `expandedQueries`。
- 默认 `RAG_QUERY_EXPANSION_PROVIDER=auto`：配置 `DASHSCOPE_API_KEY` 时使用百炼 OpenAI 兼容 Chat Completions 生成；未配置 Key 时使用本地确定性降级。
- 支持 `RAG_QUERY_EXPANSION_PROVIDER=dashscope/local` 强制指定生成方式；`dashscope` 但未配置 Key 或调用失败时仍降级到本地规则，查询不中断。
- LLM 生成时必须保留用户原问题作为第一条查询，并输出 3-5 条中文查询变体；变体应围绕用户真实意图展开，覆盖复习知识点、概念解释、步骤方法、对比差异、JD/简历证据等不同场景，而不是固定拼接“关键证据/学习资料 笔记”。
- Python 只从 LLM 响应中解析 JSON 数组或编号列表，去重、裁剪空白和过长文本后再进入 BM25 与向量召回。
- 诊断字段新增 `queryExpansionProvider/queryExpansionModel/queryExpansionFallbackReason/queryExpansionFallbackUsed/queryExpansionRequestedCount/queryExpansionReturnedCount`，用于确认本次 Multi-Query 是 LLM 生成还是本地降级。

`RagProgressVO`：

```json
{
  "stageCode": "embedding.chunk",
  "stageLabel": "生成 embedding",
  "message": "第 12/80 块：生成 embedding",
  "status": "RUNNING",
  "currentStep": 7,
  "totalSteps": 9,
  "currentChunk": 12,
  "totalChunks": 80,
  "chunkId": "material-1-11",
  "percent": 42,
  "detail": "BM25 词频已准备，正在调用 embedding 模型",
  "createdAt": "2026-06-17T10:01:12"
}
```

## Java 对外接口

### 获取 RAG 概览

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/overview` |
| 鉴权 | 必须携带 `Authorization: Bearer <token>` |
| 响应 | `Result<RagOverviewVO>` |

### 获取学习资料列表

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/materials` |
| 响应 | `Result<List<LearningMaterialVO>>` |

`LearningMaterialVO`：

```json
{
  "id": 1,
  "title": "系统设计笔记.pdf",
  "userId": "1",
  "documentType": "pdf",
  "source": "upload",
  "status": "READY",
  "parser": "mineru",
  "documentSummary": "系统设计笔记主要包含...",
  "chunkCount": 18,
  "originalFilename": "系统设计笔记.pdf",
  "originalFilePath": "uploads/rag/20260616/1-系统设计笔记.pdf",
  "storageType": "oss",
  "objectKey": "learning-evidence/1/pdf/20260617/uuid-系统设计笔记.pdf",
  "publicUrl": "https://example-cdn/learning-evidence/1/pdf/20260617/uuid-系统设计笔记.pdf",
  "latestProgress": {
    "stageCode": "vector.upsert.chunk",
    "message": "第 12/18 块：写入向量数据库",
    "currentChunk": 12,
    "totalChunks": 18,
    "percent": 78
  },
  "progressEvents": [],
  "createdAt": "2026-06-16T10:00:00",
  "updatedAt": "2026-06-16T10:01:12"
}
```

### 查询单个资料解析状态

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/materials/{id}` |
| 响应 | `Result<LearningMaterialVO>` |

用途：前端轮询或刷新单个资料的解析状态、摘要、切块数和原始文件路径。

### 预览学习资料文本内容

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/materials/{id}/preview` |
| 鉴权 | 必须携带 `Authorization: Bearer <token>` |
| Query | `source`，可选；来自 evidence 的 `sourcePath/source`，用于校验预览来源确实属于该资料 |
| 响应 | `Result<MaterialPreviewVO>` |

用途：RAG 回答和 evidence 卡片中的 Markdown 资料位置不再直接跳到 OSS/CDN 原始 URL，避免对象存储 `Content-Disposition` 或 `Content-Type` 触发浏览器下载。前端打开 `/preview/material/{id}` 新标签页后，由该接口读取当前用户名下资料的原始 Markdown/Text 内容并在应用内渲染。视频 evidence 不调用该文本预览接口，而是由前端把原始 `.mp4/.mov/.webm` 等来源链接改写到 `/videos?...startTime=...&endTime=...&videoUrl=...`，在应用内播放器按时间段定位。

`MaterialPreviewVO`：

```json
{
  "materialId": 3,
  "title": "02_llm_gateway.md",
  "documentType": "markdown",
  "source": "https://example-cdn/learning-evidence/1/markdown/20260620/uuid-02_llm_gateway.md",
  "contentType": "text/markdown",
  "content": "## 1. 题目\n..."
}
```

安全约束：

- Java 必须按当前登录用户和资料 ID 查询 `learning_material`，不能按任意外部 URL 抓取。
- 如果传入 `source`，需去掉 `#fragment` 后与该资料的 `originalFilePath/publicUrl/objectKey` 对应关系匹配，不匹配时返回业务错误。
- 现阶段只把 `markdown/md/txt/srt/vtt/text` 作为文本预览渲染；PDF、Word、PPT 和图片仍通过 evidence 摘要定位，不在该接口中转为浏览器预览；视频 evidence 使用 `/videos` 页面按 `startTime/endTime` 播放定位。

### 重建资料索引 / 高精度补跑

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/materials/{id}/reindex` |
| Query | `highPrecision`，默认 `false`；设为 `true` 时强制补跑高精度解析 |
| 响应 | `Result<LearningMaterialVO>` |

用途：对 `PARTIAL/FAILED/REINDEXING` 或用户认为解析质量不足的上传资料重新读取原始文件，重新调用 Python `/internal/rag/documents/index-file`。Java 会校验资料归属当前用户，将状态先更新为 `REINDEXING`，再从本地上传目录或 OSS `objectKey` 读取原始文件字节转发给 Python。文本手动资料没有原始文件，需通过文本索引入口重新提交。

成功时 Python 会用同一个 `documentId` 覆盖旧 RAG 切块并返回新的 `READY/PARTIAL/FAILED` 状态；失败时 Java 将资料状态标为 `FAILED` 并保留失败摘要。低质量资料修复建议前端传 `highPrecision=true`，让 Python 对 DOCX/PPTX 等资料补跑 PDF + MinerU/OCR，对视频补配 FFmpeg/ASR/OCR 后重新生成字幕 evidence 和画面 OCR evidence。

### 上传并解析入库学习资料

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/materials/upload` |
| 请求类型 | `multipart/form-data` |
| 文件字段 | `file` |
| 可选字段 | `highPrecision`，布尔值，是否强制补跑高精度解析 |
| 支持格式 | `pdf/doc/docx/ppt/pptx/md/txt/srt/vtt/xls/xlsx/png/jpg/jpeg/webp/mp4/mov/m4v/webm/mkv/avi` |
| 建议大小 | 普通资料单文件建议不超过 512MB；2 小时以上视频建议走分片上传接口 |
| 响应 | `Result<LearningMaterialVO>` |

流程：

1. Java 按 `evidence.storage.provider` 保存原始文件。生产建议使用 `oss` 上传到阿里 OSS；本地测试默认使用 `local` 写入 `uploads/` 忽略目录。
2. Java 创建 `learning_material` 记录，初始状态 `PENDING`。
3. Java 将资料状态更新为 `PARSING` 后立即返回资料记录，前端开始轮询 `/api/rag/materials/{id}` 或资料列表。
4. Java 后台调用 Python `/internal/rag/documents/index-file` 或 `/internal/rag/documents/index-video-source`，传入原始路径和高精度参数。
5. Python 按格式选择原生解析器；视频文件会优先读取同名 `.srt/.vtt/.txt` 侧车字幕或抽取内嵌字幕，字幕不可用时再尝试 FFmpeg 抽音频、百炼 ASR 生成带时间戳字幕，然后继续做候选帧采样、PPT 翻页检测、关键帧 OCR 和视频片段摘要；其他复杂版式必要时补跑 PDF + MinerU/OCR。
6. Python 在解析、递归切块、摘要、embedding 和 pgvector 写入阶段持续上报 `rag_progress` 事件；默认通过 `RAG_EVENT_CALLBACK_URL` 回调 Java `/api/logs/internal/events` 实时落入 `log_event`，回调不可用时降级直写数据库，并始终在 Python 控制台打印 `RAG进度`。
7. Python 同时把 API 入口、文件读取、解析方法、清洗、切块、摘要、每个 chunk 的 embedding/向量入库、检索和回答生成写入 `rag_process` 控制面板日志；默认同样通过 Java 内部事件接口上报，并在 Python 控制台打印 `RAG处理`。
8. Python 返回 `READY/PARTIAL/FAILED`、切块数、解析器、摘要和末次进度事件。
9. Java 按 UTF-8 读取 Python 响应体，即使响应 `Content-Type` 为 `application/octet-stream` 也按 JSON 解析；随后回写资料最终状态。失败时 Java 记录错误并补写 `index.failed` 进度；前端轮询到 `READY/PARTIAL/FAILED` 后停止展示运行中进度。

索引事务一致性：

- Python pgvector 入库必须先完成解析、递归切块、摘要和 embedding 准备，再进入数据库事务。
- `rag_document` 与 `rag_chunk` 在同一事务内替换写入；事务内校验当前 `document_id` 的实际 `rag_chunk` 数量必须等于本次切块数。
- 递归切块数为 0 时不允许写入 `rag_document` 空壳，Python 直接抛出索引失败并清理旧索引，Java 后台 worker 将 `learning_material` 标为 `FAILED`。
- 事务提交后 Python 还会再次读取 `rag_chunk` 实际数量；若提交后计数不一致，会清理本次 `rag_document/rag_chunk` 并返回失败，避免出现 `rag_document` 有记录但 `rag_chunk` 为空的假成功状态。

后端控制台打印：

- 任意格式文件上传都会通过 Python `RAG处理 | ...` 控制台日志打印处理状态，字段包含 `documentId/stage/action/filename/fileType/documentType/contentType/parser/status/blockCount/chunkCount/highPrecision/message`。
- 用户可见进度继续通过 `RAG进度 | ...` 打印当前阶段、百分比、流程步骤和切块计数。
- PPT 的 `parse.pptx` 打印格式不再是特例；PDF、Word、PPT/PPTX、表格、文本、字幕、图片和视频都会走同一套 `rag_process` 和控制台输出格式。
- 每次实际调用百炼模型前后，Python 都必须打印并上报模型事件。控制台格式保留 `RAG处理`，消息统一包含“目前在使用 xxx 模型完成 xxx 事件”或“已使用 xxx 模型完成 xxx 事件”；上下文包含 `modelProvider/modelName/modelEvent/modelPhase`。覆盖 ASR、OCR、Embedding、rerank 和 LLM 回答生成。
- OCR 会优先重试百炼模型，不会第一次失败就直接降级。第 `x/n` 次失败时，Python 同时写入 `rag_process` 和 `rag_progress`，消息格式为“第 x/n 次 OCR 失败：...，准备重试第 x+1 次”；超过 `n` 次后再记录 `WARN` 级 `*_model_degraded` 并进入本地 OCR 或跳过该帧。ASR、rerank 和回答生成存在本地或已生成 evidence 降级路径时，单次模型调用失败只记录 `WARN` 级 `*_model_degraded` 处理日志，消息包含“已降级继续处理”，不写成资料级失败；只有 embedding 等无可用降级且会中断入库的模型失败才记录 `ERROR`。

### 分片上传并解析长视频

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/materials/upload/chunk` |
| 请求类型 | `multipart/form-data` |
| 文件字段 | `file`，当前分片二进制 |
| 必填字段 | `filename/chunkIndex/totalChunks/totalSize` |
| 可选字段 | `uploadId/highPrecision` |
| 分片约束 | 前端默认 20MB 一片，后端按 `uploadId` 暂存到 `uploads/chunks` |
| 响应 | `Result<MaterialUploadChunkVO>` |

`chunkIndex` 从 `0` 开始。首片可以不传 `uploadId`，Java 会生成并返回；后续分片必须沿用同一个 `uploadId`。每个分片请求只负责把当前小文件原子写入 `uploads/chunks/{userId}/{uploadId}`，写入过程不依赖资料记录事务；写入成功的分片会保留在本地暂存目录中。若第 20 片或最后收尾步骤失败，前端继续携带同一个 `uploadId` 重试失败分片即可，不需要重新上传已成功的前 19 片。

当 `receivedChunks < totalChunks` 时，响应中的 `completed=false`，`material=null`，`status=UPLOADING`，`nextChunkIndex` 表示后端当前发现的最小缺失分片序号；前端应保存 `uploadId` 和 `nextChunkIndex`，刷新或重试时从该分片继续。当全部分片到齐后，Java 才开启一个短事务创建 `learning_material` 记录并写入 `upload.processing` 进度，然后立即返回 `completed=true`、`status=PROCESSING` 和 `material`。耗时的分片合并、阿里 OSS 上传、对象路径回写和 Python 索引触发在 Java 后台线程继续执行，避免最后一个分片请求长时间等待 OSS 上传。前端收到 `material.id` 后继续轮询 `/api/rag/materials/{id}` 读取 `upload.processing`、`PARSING` 和 Python 写入的 `rag_progress`。

响应示例：

```json
{
  "uploadId": "4d2a3e8a9f1b4a8a9d0e6f4a9b2c1d3e",
  "filename": "课程长视频.mp4",
  "chunkIndex": 7,
  "totalChunks": 18,
  "receivedChunks": 8,
  "nextChunkIndex": 8,
  "status": "UPLOADING",
  "message": "已接收视频分片：8/18",
  "completed": false,
  "material": null
}
```

全部分片到齐后的快速响应示例：

```json
{
  "uploadId": "4d2a3e8a9f1b4a8a9d0e6f4a9b2c1d3e",
  "filename": "课程长视频.mp4",
  "chunkIndex": 17,
  "totalChunks": 18,
  "receivedChunks": 18,
  "nextChunkIndex": 18,
  "status": "PROCESSING",
  "message": "视频分片已收齐，正在后台合并并上传对象存储",
  "completed": true,
  "material": {
    "id": 12,
    "title": "课程长视频.mp4",
    "documentType": "mp4",
    "status": "PENDING",
    "latestProgress": {
      "stageCode": "upload.processing",
      "message": "视频分片已收齐，正在后台合并并上传对象存储",
      "status": "RUNNING",
      "percent": 8
    }
  }
}
```

长视频分片完成后的处理链路：

1. Java 只把每个小分片原子写入临时目录，不把整段视频放入 multipart 请求体；单片写入成功后即独立持久化，不受后续资料记录事务影响。
2. 最后一个分片到达后，Java 创建资料记录并返回前端，避免 HTTP 请求继续等待阿里 OSS 上传；如果资料记录创建或后台调度失败，已上传分片仍留在 `uploads/chunks`，可用同一 `uploadId` 重试。
3. Java 后台线程按分片顺序合并文件，将视频文件流式保存到本地上传目录或阿里 OSS，并回写 `learning_material.original_file_path/storage_type/object_key/public_url`。
4. 对视频文件，Java 调用 Python `/internal/rag/documents/index-video-source`，只传 `sourcePath/filename` 等元数据，避免再次把整段视频读成 `byte[]`。
5. Python 根据本地路径或公开视频 URL 处理视频源：优先读取同名 `.srt/.vtt/.txt` 侧车字幕或转写文本，其次尝试抽取视频内嵌字幕；字幕不可用且有公开视频 URL 时才用百炼 filetrans 处理公开视频；本地或私有源按固定时长切音频段，分段前后保留重叠上下文，逐段 ASR 后按全局时间轴合并并去重。
6. Python 继续抽取候选帧、PPT 翻页检测、关键帧 OCR 和视频片段摘要，最终按统一 evidence 结构入库。

常见失败：

| 场景 | 返回 |
| --- | --- |
| `chunkIndex` 越界或 `totalChunks <= 0` | `Result.error("分片参数不合法")` |
| 同一 `uploadId` 下文件名或分片总数不一致 | `Result.error("分片上传状态不一致")` |
| 某个分片请求网络中断 | 前端保存 `uploadId` 后从失败的 `chunkIndex` 重试，后端保留已成功分片 |
| 全部分片收齐后创建资料记录失败 | 已上传分片保留在暂存目录，前端用同一 `uploadId` 重试最后一个分片触发收尾 |
| 合并后保存对象存储失败 | 资料状态写为 `FAILED/upload-chunk-error`，分片目录暂不清理；前端用同一 `uploadId` 重试最终分片时，Java 复用 `material.id` 并重新调度后台收尾 |
| Python 长视频索引超时或部分失败 | 资料状态写为 `FAILED/PARTIAL`，日志保留 `errorLocation` |

### 直接索引文本资料

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/materials/text` |
| 请求体 | `RagIndexTextDTO` |
| 响应 | `Result<LearningMaterialVO>` |

请求示例：

```json
{
  "title": "Spring Boot 项目笔记",
  "documentType": "markdown",
  "source": "manual",
  "visibilityScope": "private",
  "content": "## IOC\nSpring 容器负责对象创建与依赖注入..."
}
```

### 查询资料 evidence

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/materials/{id}/evidences` |
| Query | `limit`，默认 20，最大 100 |
| 响应 | `Result<List<RagEvidenceVO>>` |

用途：展示某个资料已入库的 evidence 元数据和片段。

### RAG 检索问答

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/query` |
| 请求体 | `RagQueryDTO` |
| 响应 | `Result<RagQueryVO>` |

请求示例：

```json
{
  "question": "BM25 和向量检索如何融合？",
  "topK": 5,
  "candidateMultiplier": 4,
  "metadataFilter": {
    "documentType": "markdown",
    "source": "upload",
    "sectionKeyword": "RAG-Fusion"
  }
}
```

查询参数归一化：

| 字段 | 规则 |
| --- | --- |
| `topK` | 最终 evidence 数，默认 `5`，Java 和 Python 均限制在 `1-20`。 |
| `candidateMultiplier` | RRF/RAG-Fusion 后进入 rerank 前的候选倍率，默认 `4`，限制在 `2-10`。候选预算为 `max(topK * candidateMultiplier, 20)`；默认仍保持旧行为 `max(topK * 4, 20)`。 |
| `metadataFilter` | 只接收白名单字段；空字符串、空数组、`null` 和未知字段会被删除，并记录到诊断字段 `ignoredMetadataFilterKeys`。字符串数组表示 `IN`，单值表示精确匹配。 |

`metadataFilter` 白名单：

| 字段 | 匹配语义 |
| --- | --- |
| `documentId` | 精确匹配或 `IN`，用于资料内检索等内部场景；第一阶段前端不暴露。 |
| `documentType` | 精确匹配或 `IN`。 |
| `source` | 精确匹配或 `IN`。 |
| `parser` | 精确匹配或 `IN`。 |
| `mediaType` | 精确匹配或 `IN`。 |
| `evidenceChannel` | 精确匹配或 `IN`，如 `subtitle/frame_ocr/video_segment_summary`。 |
| `blockType` | 精确匹配或 `IN`。 |
| `sectionName` | 精确匹配或 `IN`。 |
| `sectionKeyword` | 大小写不敏感的包含匹配，匹配 `sectionName` 或 `metadata.sectionTitle`。 |
| `pageIndex` | 精确匹配或 `IN`，Java 和 Python 均按字符串比较，兼容前端传数字或字符串。 |
| `slideIndex` | 精确匹配或 `IN`，Java 和 Python 均按字符串比较，兼容前端传数字或字符串。 |

权限边界：

- Java 会覆盖 `metadataFilter.userId` 为当前登录用户 ID，并固定 `visibilityScope=private`，不接受前端覆盖。
- Python 查询诊断只展示 `systemMetadataFilter.visibilityScope=private`，不向普通用户展示 `userId`。
- 前端第一阶段只暴露 `documentType/source/evidenceChannel/blockType/sectionKeyword/topK/candidateMultiplier`，但 Java/Python 支持的白名单保持一致。

### RAG 回答准入与拒答契约

`/api/rag/query`、`/api/rag/query/tasks/{taskId}` 和 Python `/internal/rag/query` 都返回同一套回答准入字段。拒答不是异常：HTTP 与 Java `Result<T>` 仍为成功业务结果，任务状态仍为 `COMPLETED`，只通过 `answerStatus=REFUSED` 表示本次知识库证据不足。

`RagQueryVO`、`RagQueryHistoryVO` 和 Python `QueryResponse` 新增字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `answerStatus` | `"ANSWERED" \| "REFUSED"` | 本轮查询是否有足够 evidence 支撑回答。第一版只返回这两个值，`PARTIAL` 暂不返回。 |
| `refusalReason` | `string \| null` | `REFUSED` 时返回拒答原因，`ANSWERED` 时为空。 |
| `refusalPolicy` | `string` | 初始固定为 `STRICT_EVIDENCE_GUARD_V1`，用于后续阈值和策略演进。 |
| `confidence` | `number` | Python guard 计算的 `answerableScore`，范围 `0..1`。 |
| `supportingEvidenceIds` | `string[]` | 真正进入回答生成的 evidenceId。拒答时为空。 |
| `refusalMessage` | `string \| null` | 给前端标签、历史列表和摘要使用的短拒答文案。 |

拒答原因枚举：

| 原因 | 触发语义 |
| --- | --- |
| `NO_EVIDENCE` | 当前用户作用域内候选 chunk 总数为 0，即 `totalCandidateChunkCount=0`。 |
| `FILTERED_OUT` | 用户作用域内有 chunk，但业务过滤后为 0，即 `totalCandidateChunkCount>0 && filteredChunkCount=0`。 |
| `LOW_CONFIDENCE` | 召回、融合或重排后有候选，但 Top evidence 的归一化可回答分低于门槛。 |
| `INSUFFICIENT_COVERAGE` | Top evidence 分数达标，但有效支持证据数不足或问题关键词覆盖不足。 |
| `WEAK_SNIPPET` | Top evidence 的 snippet 为空、过短，或主要是标题、目录、时间戳等不可回答内容。 |
| `ONLY_DIAGNOSTIC_CANDIDATES` | 候选全部是 summary child、重复视频帧或 diversity 剔除后的残留诊断项，不足以支撑回答。 |
| `UNKNOWN` | guard 发生可恢复异常时保守拒答，避免异常绕过准入后直接生成。 |

字段语义：

- `evidences` 只表示“支持本次回答的证据”。`answerStatus=REFUSED` 时必须返回 `evidences=[]`，Java `evidenceCount=0`。
- 低相关候选只进入 `diagnostics.answerGuard.candidateEvidenceSummaries`，不得放入顶层 `evidences`，避免 JD 分析、简历补丁和 Agent 工具误用。
- `answer` 在拒答时是面向用户的中文拒答正文；`refusalMessage` 是同语义短文案。
- Python guard 位于 `dedupe_evidences_for_context()` 之后、`generate_grounded_answer()` 之前。`REFUSED` 时不调用 LLM；`ANSWERED` 时只把 `supportingEvidenceIds` 对应 evidence 送入 LLM。

默认阈值：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RAG_ANSWER_MIN_ANSWERABLE_SCORE` | `0.45` | 综合可回答分最低门槛。 |
| `RAG_ANSWER_MIN_TOP_SCORE_DASHSCOPE` | `0.35` | 百炼 rerank 归一化 Top 分门槛。 |
| `RAG_ANSWER_MIN_TOP_SCORE_LOCAL` | `0.25` | 本地 rerank fallback 归一化 Top 分门槛。 |
| `RAG_ANSWER_MIN_KEYWORD_COVERAGE` | `0.08` | 问题关键词和扩展查询关键词在候选标题、章节、snippet 中的最低覆盖率。 |
| `RAG_ANSWER_MIN_SUPPORTING_EVIDENCE_COUNT` | `1` | 第一版最小支持证据数量。 |
| `RAG_ANSWER_STRICT_MODE` | `true` | 默认开启严格准入。 |

`diagnostics.answerGuard` 结构示例：

```json
{
  "answerStatus": "REFUSED",
  "refusalReason": "LOW_CONFIDENCE",
  "refusalPolicy": "STRICT_EVIDENCE_GUARD_V1",
  "confidence": 0.21,
  "supportingEvidenceIds": [],
  "candidateEvidenceSummaries": [
    {
      "evidenceId": "material-3-summary-0001",
      "title": "RAG 优化笔记",
      "sectionName": "Post-Retrieval",
      "score": 0.19,
      "snippetPreview": "父段摘要：RAG-Fusion 使用..."
    }
  ],
  "thresholds": {
    "minAnswerableScore": 0.45,
    "minTopScore": 0.25,
    "minKeywordCoverage": 0.08,
    "minSupportingEvidenceCount": 1,
    "strictMode": true
  },
  "signals": {
    "normalizedTopScore": 0.19,
    "keywordCoverage": 0.0,
    "evidenceCountSignal": 1.0,
    "snippetQualitySignal": 0.72,
    "riskPenalty": 0.2,
    "answerableScore": 0.21
  },
  "message": "当前知识库没有检索到足够相关的证据，无法基于个人资料回答该问题。"
}
```

### 创建 RAG 检索任务

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/query/tasks` |
| 请求体 | `RagQueryDTO` |
| 响应 | `Result<RagQueryTaskVO>` |

用途：前端提交检索后立刻获得 `taskId`，随后轮询任务状态，不再等待同步问答接口阻塞。

Java 调用 Python：

| 项目 | 内容 |
| --- | --- |
| 创建任务 | `POST /internal/rag/query/tasks` |
| 轮询任务 | `GET /internal/rag/query/tasks/{taskId}` |
| 任务存储 | Python 进程内短期内存，默认按最近更新时间保留约 30 分钟 |
| 事件来源 | Python `RagProgressReporter.on_emit`，不是 Java 推测进度 |
| 前端行为 | 创建任务后每 `300-500ms` 轮询，按 `progressEvents` 原始顺序展示阶段详情 |

响应示例：

```json
{
  "taskId": "7d1b0e4a4e594a1ab4d9d5e0c9c0f2ab",
  "status": "RUNNING",
  "message": "正在执行 RAG 检索问答",
  "progressEvents": [
    {
      "stageCode": "query.expand",
      "stageLabel": "Multi-Query",
      "message": "准备生成 Multi-Query 查询变体",
      "status": "RUNNING",
      "currentStep": 1,
      "totalSteps": 8,
      "percent": 6
    }
  ],
  "result": null,
  "errorMessage": null
}
```

### 轮询 RAG 检索任务

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/query/tasks/{taskId}` |
| 响应 | `Result<RagQueryTaskVO>` |

`status` 取值：

| 状态 | 含义 |
| --- | --- |
| `RUNNING` | Java 后台仍在调用 Python 或等待最终响应 |
| `COMPLETED` | Python 查询完成，`result` 为完整 `RagQueryVO` |
| `FAILED` | 查询失败，`errorMessage` 为失败摘要 |
| `EXPIRED` | 临时任务不存在或已过期 |

前端轮询建议间隔 `300-500ms`。任务完成或失败后停止轮询。查询任务只用于当前 Python 进程内短期展示，不作为业务持久化记录；页面刷新后可重新发起查询。

Java 在 `POST /api/rag/query/tasks` 创建任务时写入一条 `rag_query_history`，状态为 `RUNNING`；前端后续轮询 `GET /api/rag/query/tasks/{taskId}` 时，Java 会在任务进入 `COMPLETED/FAILED/EXPIRED` 后回写历史记录。历史记录是业务查询快照，供用户查看最近几次询问，不替代 Python 的实时进度任务。

任务查询的 `result` 与同步 `RagQueryVO` 完全一致，包含 `answerStatus/refusalReason/refusalPolicy/confidence/supportingEvidenceIds/refusalMessage`。当 `answerStatus=REFUSED` 时，任务仍为 `COMPLETED`，`result.evidences=[]`。

### 查询 RAG 询问历史

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/query/history` |
| Query | `startDate`、`endDate`、`limit` |
| 响应 | `Result<List<RagQueryHistoryVO>>` |

用途：按当前登录用户和日期范围查询最近几次 RAG 询问，前端可让用户自行选择“从/到/条数”并点击历史项回填回答和证据。

查询参数：

| 参数 | 说明 |
| --- | --- |
| `startDate` | 本地日期，格式 `YYYY-MM-DD`。为空时默认最近 7 天起始日。 |
| `endDate` | 本地日期，格式 `YYYY-MM-DD`。为空时默认当天。 |
| `limit` | 返回条数，默认 5，最小 1，最大 50。 |

Java 会把日期范围限制在最近 7 天内，查询条件为 `created_at >= startDate 00:00:00` 且 `< endDate + 1 day 00:00:00`，并按 `created_at DESC, id DESC` 返回。

响应示例：

```json
{
  "code": 1,
  "msg": null,
  "data": [
    {
      "id": 12,
      "taskId": "7d1b0e4a4e594a1ab4d9d5e0c9c0f2ab",
      "question": "BM25 和向量检索如何融合？",
      "answer": "可以通过 RRF/RAG-Fusion 合并多路召回结果...",
      "answerStatus": "ANSWERED",
      "refusalReason": null,
      "refusalPolicy": "STRICT_EVIDENCE_GUARD_V1",
      "confidence": 0.82,
      "supportingEvidenceIds": ["material-1:chunk-3"],
      "refusalMessage": null,
      "status": "COMPLETED",
      "topK": 6,
      "evidenceCount": 3,
      "expandedQueries": ["BM25 和向量检索如何融合？"],
      "evidences": [
        {
          "evidenceId": "material-1:chunk-3",
          "title": "RAG 优化笔记",
          "sectionName": "Post-Retrieval",
          "snippet": "RAG-Fusion 使用多重查询和倒数排名融合..."
        }
      ],
      "diagnostics": {
        "answerProvider": "dashscope"
      },
      "createdAt": "2026-06-20T10:30:00",
      "updatedAt": "2026-06-20T10:30:08"
    }
  ]
}
```

失败响应示例：

```json
{
  "code": 0,
  "msg": "查询 RAG 询问历史 [rag_query/history/rag_query_history_query] 失败：登录状态已失效",
  "data": null
}
```

历史查询只读取 Java 业务库，不调用 Python；`RagQueryHistoryVO.evidences` 与 `RagQueryVO.evidences` 使用同一 evidence 引用结构，保留资料标题、章节、片段、来源和分数。数据库不新增列，`rag_query_history.diagnostics_json.answerGuard` 保存 Python guard 结构，Java 读取历史时从该结构回填 `answerStatus/refusalReason/refusalPolicy/confidence/supportingEvidenceIds/refusalMessage` 顶层字段。旧历史或旧 Python 响应缺字段时，Java 兼容为：有 evidence 默认为 `ANSWERED`，无 evidence 默认为 `REFUSED`。

响应新增 `diagnostics`，用于前端和调试确认检索链路：

```json
{
  "answerProvider": "dashscope",
  "answerModel": "qwen-plus",
  "queryExpansionProvider": "dashscope",
  "queryExpansionModel": "qwen-plus",
  "queryExpansionFallbackUsed": false,
  "rerankProvider": "dashscope",
  "rerankModel": "qwen3-rerank",
  "totalCandidateChunkCount": 80,
  "filteredChunkCount": 42,
  "effectiveMetadataFilter": {
    "documentType": "markdown",
    "sectionKeyword": "RAG-Fusion"
  },
  "systemMetadataFilter": {
    "visibilityScope": "private"
  },
  "ignoredMetadataFilterKeys": ["unknownKey", "emptyKey"],
  "candidateBudget": 20,
  "rerankedCandidateCount": 12,
  "dedupRemovedCount": 3,
  "dedupGroupCount": 2,
  "diversityPolicy": "video_duplicate_group_and_time_window",
  "answerGuard": {
    "answerStatus": "ANSWERED",
    "refusalReason": null,
    "refusalPolicy": "STRICT_EVIDENCE_GUARD_V1",
    "confidence": 0.82,
    "supportingEvidenceIds": ["material-2-parent-text-0001"],
    "thresholds": {
      "minAnswerableScore": 0.45,
      "minTopScore": 0.35,
      "minKeywordCoverage": 0.08,
      "minSupportingEvidenceCount": 1,
      "strictMode": true
    },
    "signals": {
      "normalizedTopScore": 0.79,
      "keywordCoverage": 0.24,
      "evidenceCountSignal": 1.0,
      "snippetQualitySignal": 1.0,
      "riskPenalty": 0.0,
      "answerableScore": 0.82
    }
  },
  "parentAggregation": {
    "enabled": true,
    "matchedChildCount": 18,
    "expandedParentCount": 5,
    "prerequisiteExpansionEnabled": false
  },
  "matchedChildIds": ["material-2-11", "material-2-summary-0001"],
  "expandedParentIds": ["material-2-parent-text-0001"],
  "prerequisiteAddedIds": []
}
```

其中 `totalCandidateChunkCount` 表示当前用户权限范围内的总候选切块数，`filteredChunkCount` 表示业务过滤后的候选切块数，`effectiveMetadataFilter` 表示真正生效的业务过滤项，`systemMetadataFilter` 只展示非敏感系统范围，`ignoredMetadataFilterKeys` 表示被删除的未知或空过滤字段。`candidateBudget` 表示 RRF/RAG-Fusion 后进入 rerank 的候选证据预算；`parentAggregation`、`matchedChildIds`、`expandedParentIds` 用于说明子块命中后是否展开到父段上下文；`prerequisiteAddedIds` 现阶段固定为空，Stage 2 前置知识扩展默认关闭；`dedupRemovedCount`、`dedupGroupCount` 和 `diversityPolicy` 用于说明查询阶段是否移除了视频近重复 evidence。

`RagQueryVO` 响应示例：

```json
{
  "answer": "基于 evidence 的 Markdown 回答正文，保留 **加粗**、列表和 [evidenceId=material-2-11] 引用。",
  "answerStatus": "ANSWERED",
  "refusalReason": null,
  "refusalPolicy": "STRICT_EVIDENCE_GUARD_V1",
  "confidence": 0.82,
  "supportingEvidenceIds": ["material-2-11"],
  "refusalMessage": null,
  "expandedQueries": [
    "自注意力机制的核心原理是什么？",
    "Transformer 中 Query、Key、Value 如何计算注意力权重？",
    "为什么自注意力适合建模长距离依赖？",
    "复习自注意力时需要掌握哪些公式和例子？"
  ],
  "evidences": [
    {
      "evidenceId": "material-2-11",
      "documentId": "material-2",
      "title": "01_transform_attention.md",
      "sectionName": "1.1 自注意力机制到底在做什么",
      "documentType": "markdown",
      "score": 0.7162,
      "retrievalSource": "rerank",
      "parseEngine": "markdown"
    }
  ],
  "diagnostics": {
    "answerProvider": "dashscope",
    "answerModel": "qwen-plus",
    "queryExpansionProvider": "dashscope",
    "queryExpansionModel": "qwen-plus",
    "queryExpansionFallbackUsed": false,
    "filteredChunkCount": 42,
    "candidateBudget": 20,
    "parentAggregation": {
      "enabled": true,
      "matchedChildCount": 2,
      "expandedParentCount": 1,
      "prerequisiteExpansionEnabled": false
    },
    "matchedChildIds": ["material-2-11", "material-2-summary-0001"],
    "expandedParentIds": ["material-2-parent-text-0001"],
    "prerequisiteAddedIds": []
  },
  "progressEvents": [
    {
      "stageCode": "query.expand",
      "stageLabel": "Multi-Query",
      "message": "正在生成 Multi-Query 查询变体",
      "status": "RUNNING",
      "currentStep": 1,
      "totalSteps": 8,
      "percent": 8
    },
    {
      "stageCode": "query.guard",
      "stageLabel": "回答准入",
      "message": "回答准入通过：可回答分 0.8200，支持证据 1 条",
      "status": "COMPLETED",
      "currentStep": 7,
      "totalSteps": 8,
      "percent": 88
    },
    {
      "stageCode": "query.answer",
      "stageLabel": "回答生成",
      "message": "RAG 检索问答完成",
      "status": "COMPLETED",
      "currentStep": 8,
      "totalSteps": 8,
      "percent": 100
    }
  ]
}
```

拒答响应示例：

```json
{
  "answer": "当前知识库没有检索到足够相关的证据，无法基于个人资料回答该问题。请补充相关学习资料后重试。",
  "answerStatus": "REFUSED",
  "refusalReason": "LOW_CONFIDENCE",
  "refusalPolicy": "STRICT_EVIDENCE_GUARD_V1",
  "confidence": 0.21,
  "supportingEvidenceIds": [],
  "refusalMessage": "证据不足，已拒答",
  "expandedQueries": ["酸面包 autolyse hydration schedule"],
  "evidences": [],
  "diagnostics": {
    "answerGuard": {
      "answerStatus": "REFUSED",
      "refusalReason": "LOW_CONFIDENCE",
      "candidateEvidenceSummaries": [
        {
          "evidenceId": "material-2-summary-0001",
          "title": "RAG 优化笔记",
          "sectionName": "Post-Retrieval",
          "score": 0.19
        }
      ]
    }
  },
  "progressEvents": [
    {
      "stageCode": "query.guard",
      "stageLabel": "回答准入",
      "message": "回答准入拒答：LOW_CONFIDENCE，可回答分 0.2100",
      "status": "COMPLETED",
      "currentStep": 7,
      "totalSteps": 8,
      "percent": 88
    },
    {
      "stageCode": "query.answer",
      "stageLabel": "回答生成",
      "message": "证据不足，已跳过 LLM 回答生成",
      "status": "COMPLETED",
      "currentStep": 8,
      "totalSteps": 8,
      "percent": 100
    }
  ]
}
```

### 运行 JD 适配分析

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/page-data/jd-analysis/analyze` |
| 鉴权 | 必须携带 `Authorization: Bearer <token>` |
| 请求体 | `JdAnalysisRequestDTO` |
| 响应 | `Result<JdAnalysisVO>` |

请求示例：

```json
{
  "jobDescription": "需要熟悉 RAG-Fusion、Multi-Query、BM25 和向量检索的 AI 应用开发实习生。",
  "resumeText": "做过 RAG-Fusion 检索增强项目，使用 Spring Boot 和 FastAPI 联调。"
}
```

处理流程：

1. Java 解析当前登录用户 ID。
2. Java 调用 Python `/internal/rag/jd-analysis`，传入 `userId/jobDescription/resumeText/topK`。
3. Python 从 JD 抽取技能项，对每个技能在当前用户知识库中执行 RAG 检索。
4. Python 输出 `supported/weak/missing`、学习计划和简历证据对齐。
5. Java 保存 `jd_analysis_report`、`jd_analysis_skill`、`jd_learning_plan_item` 和 `resume_evidence_alignment`。
6. 前端展示最新匹配度、已掌握/半掌握/缺口、学习计划和证据对齐矩阵。

## Python 内部接口

### 健康检查

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | FastAPI 服务健康检查 |

### 解析并入库文件

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/rag/documents/index-file` | 接收 Java 转发文件，解析为 `DocumentBlock` 后切块、索引、存储 evidence |
| `POST` | `/internal/rag/documents/index-video-source` | 接收 Java 已保存的视频来源路径，按视频源解析并索引 evidence |

`multipart/form-data` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file | 是 | 原始文件 |
| `document_id` | string | 是 | Java 资料 ID，形如 `material-1` |
| `title` | string | 是 | 资料标题 |
| `document_type` | string | 是 | 文件类型或业务类型 |
| `source` | string | 是 | `upload/manual/import` 等 |
| `user_id` | string | 是 | Java 当前登录用户 ID |
| `visibility_scope` | string | 是 | `private/public/team` |
| `source_path` | string | 否 | Java 保存的原始文件路径 |
| `high_precision` | bool | 否 | 强制补跑 PDF + MinerU/OCR |

`source_path` 在 OSS 模式下优先传入可访问的 OSS/CDN URL；如果未配置公开访问地址，则传入 `oss://bucket/objectKey`，用于 evidence 来源追踪。真实视频播放需要 `ALIYUN_OSS_PUBLIC_BASE_URL` 指向可被浏览器访问的公开域名或后续补充签名 URL 服务。

### 按视频源索引

`POST /internal/rag/documents/index-video-source` 使用 `application/json`，用于长视频分片合并后避免 Java 再次转发完整文件。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `documentId` | string | 是 | Java 资料 ID，形如 `material-1` |
| `title` | string | 是 | 资料标题 |
| `documentType` | string | 是 | 视频文件类型，如 `mp4/mkv/webm` |
| `source` | string | 是 | 固定为 `upload` |
| `userId` | string | 是 | Java 当前登录用户 ID |
| `visibilityScope` | string | 是 | `private/public/team` |
| `sourcePath` | string | 是 | 本地绝对/相对路径、公开视频 URL 或 `oss://bucket/key` |
| `filename` | string | 否 | 原始文件名 |
| `contentType` | string | 否 | MIME 类型 |
| `highPrecision` | bool | 否 | 预留字段，视频当前主要控制 ASR/OCR 补跑 |

`sourcePath` 为本地路径时，Python 直接从文件系统读取；为公开视频 URL 时，Python 优先走百炼异步 filetrans，并用 FFmpeg 从 URL 抽取关键帧。`oss://` 且无公开 URL 时，当前只作为来源追踪，Python 无法直接读取对象内容，Java 需配置 `ALIYUN_OSS_PUBLIC_BASE_URL` 或后续补充签名 URL 服务。

### 索引文本

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/rag/documents/index-text` | 接收已提取文本，转换为 `DocumentBlock` 后建立索引 |

### 查询文档 evidence

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/internal/rag/documents/{document_id}/evidences?limit=20` | 返回某个文档已入库 evidence |

### 检索问答

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/rag/query` | Multi-Query + BM25/向量混合检索 + RRF 融合 |

### JD 适配分析

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/rag/jd-analysis` | 从 JD 抽取技能项，按当前用户知识库检索 evidence，输出技能状态、学习计划和简历证据对齐 |

请求示例：

```json
{
  "userId": "1",
  "jobDescription": "需要熟悉 RAG-Fusion、Multi-Query 和 BM25。",
  "resumeText": "做过 RAG-Fusion 检索增强项目。",
  "topK": 3
}
```

### 概览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/internal/rag/overview` | 返回 RAG 仓库统计 |

## Java 调 Python 契约

| Java 动作 | Python Endpoint | 超时 | 重试 | 幂等键 |
| --- | --- | --- | --- | --- |
| 文本索引 | `POST /internal/rag/documents/index-text` | 60s | 不自动重试 | `documentId` |
| 文件解析入库 | `POST /internal/rag/documents/index-file` | 60s | 不自动重试 | `documentId` |
| 视频源解析入库 | `POST /internal/rag/documents/index-video-source` | `index-timeout-seconds` | 不自动重试 | `documentId` |
| 重建资料索引/补跑修复 | `POST /internal/rag/documents/index-file` 或 `index-video-source` | `index-timeout-seconds` | 不自动重试 | `documentId` |
| 资料 evidence | `GET /internal/rag/documents/{document_id}/evidences` | 30s | 不自动重试 | 无 |
| 检索问答 | `POST /internal/rag/query` | 30s | 不自动重试 | 无 |
| JD 适配分析 | `POST /internal/rag/jd-analysis` | 30s | 不自动重试 | 无 |
| 概览同步 | `GET /internal/rag/overview` | 5s | 不自动重试 | 无 |

Java 读取 Python 响应时统一按 `byte[]` 接收并使用 UTF-8 解码为 JSON，避免 Python/FastAPI 或异常响应以 `application/octet-stream` 返回时触发 Spring `String` 消息转换失败。

Python `IndexResponse`：

```json
{
  "documentId": "material-1",
  "title": "系统设计笔记.pdf",
  "status": "READY",
  "chunkCount": 18,
  "parser": "mineru",
  "documentSummary": "系统设计笔记主要包含...",
  "parseQuality": {
    "score": 0.92,
    "nativeTextChars": 8120,
    "paragraphCount": 86,
    "tableCount": 4,
    "imageCount": 8,
    "screenshotLike": false,
    "needsSupplement": false,
    "messages": [
      "video.audio.extract: FFmpeg 提取音频失败: ...",
      "video.frame_ocr[2]: Bailian OCR returned empty text"
    ]
  },
  "progressEvents": [
    {
      "stageCode": "chunk.recursive",
      "stageLabel": "递归切块",
      "message": "当前文件被切分为 18 块",
      "status": "COMPLETED",
      "currentChunk": 0,
      "totalChunks": 18,
      "percent": 35
    }
  ]
}
```

`parseQuality.messages` 用于透传 Python 解析阶段的可定位告警。视频链路约定使用 `video.audio.extract`、`video.asr`、`video.frame.extract`、`video.slide_detect`、`video.frame_ocr[n]`、`video.segment_summary`、`video.fallback` 等位置前缀。Java 读取这些消息后会在 `PARTIAL` 时写入 `log_error`，上下文包含 `errorLocation`，方便定位报错环节。为避免长视频上千个 chunk 重复携带同一告警，Python 入库到 `rag_chunk.metadata.parseQuality` 时只保留质量分数和 `messageCount`，完整 `messages` 仅保留在索引接口响应、Java 日志上下文和资料级状态同步中。

错误映射：

| Python 状态 | Java Result |
| --- | --- |
| `400` | `Result.error("RAG 请求参数无效")` |
| `404` | `Result.error("未找到可检索资料")` |
| `5xx` | `Result.error("Python RAG 服务暂不可用，请稍后重试")` |
| 超时 | `Result.error("Python RAG 服务响应超时")` |

## DocumentBlock

所有解析器最终输出统一 `DocumentBlock`：

```json
{
  "documentId": "material-1",
  "blockId": "material-1-docx-p12",
  "fileType": "docx",
  "blockType": "text",
  "pageIndex": null,
  "slideIndex": null,
  "sheetName": null,
  "cellRange": null,
  "startTime": null,
  "endTime": null,
  "sectionTitle": "项目背景",
  "contentText": "本项目围绕多模态学习证据库...",
  "contentHtml": null,
  "assetPath": null,
  "bbox": null,
  "parseEngine": "python-docx",
  "confidence": 0.88,
  "sourceTitle": "系统设计笔记.docx",
  "sourcePath": "uploads/rag/20260616/1-系统设计笔记.docx",
  "metadata": {
    "paragraphIndex": 12,
    "style": "Normal"
  }
}
```

字幕或转写文本解析出的 `DocumentBlock` 会写入 `startTime/endTime`，时间格式保持原字幕文本中的 `HH:MM:SS` 或 `MM:SS` 表达。`blockType` 取值：`heading/text/table/image/chart/formula/code/list`。

## 父子索引与 summary child

Stage 1 索引统一使用父子结构，但不新增 Agent 编排、长任务调度或工具调用。Python 在递归切块阶段为文本和视频 chunk 补齐父段字段，并把摘要作为一等可召回子块写入 BM25 与 embedding：

- 文本父段优先按 Markdown heading、解析器 sectionTitle、页面/幻灯片章节构建；没有标题时退化为段落窗口。父段 metadata 包含 `parentSegmentId`、`parentKind=text_section/text_window`、`parentStartTime=null`、`parentEndTime=null`。
- 视频父段按字幕/ASR 时间窗口、视频片段摘要和 OCR 出现时间构建。父段 metadata 包含 `parentSegmentId`、`parentKind=video_segment`、`parentStartTime`、`parentEndTime`。
- 原文、字幕 ASR、表格、代码、图片 OCR 等切块统一标记 `childKind=raw` 或更具体的 `ocr_occurrence`、`video_segment_summary`。
- `SummaryIndex.build()` 仍生成 `rag_document.document_summary` 和 `section_summaries`，同时 Python 会基于父段生成 `childKind=summary` 的 summary child。summary child 必须与 raw child 一样写入 BM25、term_counts 和 embedding，不能只停留在资料摘要字段。
- 视频 `evidenceChannel=video_segment_summary` 的解析块视为可检索 summary child，`childKind=video_segment_summary`，不再作为只展示的轻量摘要。
- 新增 metadata keys 保留：`parentSegmentId`、`parentStartTime`、`parentEndTime`、`parentKind`、`childKind`、`occurrenceId`、`occurrenceTime`、`retrievalLayer`、`concepts`、`segmentRole`、`prerequisiteSegmentIds`、`relatedSegmentIds`、`matchedChildIds`、`matchedChildKinds`、`linkedVisualGroupIds`、`linkedDuplicateGroupIds`。
- `segmentRole` 当前仅做 metadata 标注，合法值为 `intro|definition|basic|explanation|example|application|derivation|advanced|review|chitchat|unknown`；Stage 2 prerequisite/base-advanced 扩展默认关闭，`prerequisiteAddedIds` 为空。

视频 OCR occurrence 建模：

- 入库前视频 OCR 仍可按文本近重复得到代表 `DocumentBlock`，并保留 `timeRanges/sourceFrameTimes`。
- 切块阶段会把每个 OCR-confirmed `sourceFrameTimes` 或 `timeRanges` 展开为独立 occurrence child。每个 child 拥有稳定 `occurrenceId`、`occurrenceTime` 和 occurrence 所在时间段的 `parentSegmentId`。
- 同一视觉/OCR 内容在第 10 分钟和第 90 分钟出现时，会生成两个 occurrence child，分别挂到对应视频父段；查询 diversity 优先按 `occurrenceId` 分组，其次按 `parentSegmentId + duplicateGroupId`，最后才沿用旧 duplicate/hash 逻辑。

父段聚合：

- memory retriever 与 pgvector retriever 共用 Python `parent_aggregation` helper。RRF/RAG-Fusion 得到的是子块候选，进入 rerank 前先聚合为父段 evidence。
- 聚合 evidence 的 `retrievalSource` 仍使用现有合法枚举 `fusion`，不新增 `parent` 枚举；命中的原始层级写入 `metadata.retrievalLayer=parent_aggregated`，并带上 `matchedChildIds`、`matchedChildKinds`。
- 如果命中子块没有父段字段，helper 会按原 evidence 透传，并写入 `metadata.retrievalLayer=child`。
- 诊断信息必须包含 `parentAggregation`、`matchedChildIds`、`expandedParentIds`，可选 `prerequisiteAddedIds`；Stage 1 默认不启用 prerequisite 扩展。

## Evidence 结构

检索和资料 evidence 查询统一返回：

```json
{
  "evidenceId": "material-1-3",
  "documentId": "material-1",
  "documentTitle": "系统设计笔记.pdf",
  "blockId": "material-1-p2-b4",
  "blockType": "table",
  "pageIndex": 2,
  "slideIndex": null,
  "sheetName": null,
  "cellRange": null,
  "startTime": null,
  "endTime": null,
  "sectionTitle": "RAG 入库流程",
  "snippet": "原始文件 -> 解析路由 -> DocumentBlock -> 递归切块...",
  "sourcePath": "uploads/rag/20260616/1-系统设计笔记.pdf",
  "assetPath": null,
  "playbackUrl": null,
  "score": 0.0327,
  "retrievalSource": "fusion",
  "parseEngine": "mineru"
}
```

视频字幕、ASR 转写文本或关键帧 OCR 命中时，`startTime/endTime` 用于展示视频证据所在时间段，例如 `01:23:10-01:25:42`。前端只在 evidence 存在 `startTime` 且能从 `playbackUrl`、`sourcePath` 或明确的视频 `source` 构造播放入口时展示“播放定位”按钮；普通网页、`upload`、`manual` 或私有 `oss://` 来源不会被当作可播放视频地址。`playbackUrl` 可以是内部 `/videos?...` 链接，也可以是外部公开视频 URL 加 `#t=` 秒点。兼容字段：Java/前端仍可读取 `title`、`source`、`sectionName`、`documentType`，其值分别映射自 `documentTitle`、`sourcePath/source`、`sectionTitle` 和资料类型。

Python `Evidence.metadata` 会保留上述父子索引、OCR occurrence、聚合诊断和视频去重字段。当前 Java `PythonRagClient` / `RagEvidenceVO` 只透传固定 evidence 字段，未把完整 `metadata` 暴露给前端；因此本阶段 metadata 可见性保证在 Python 内部、Python query/list 响应和 `diagnostics`，Java/前端页面只依赖既有字段展示。后续如需前端展示 `retrievalLayer` 或 `matchedChildIds`，必须先扩展 `RagEvidenceVO` 和 TypeScript 类型。

视频 evidence 前端统一跳转到 `/videos` 播放页，query 参数使用 `URLSearchParams` 编码：

| 参数 | 来源 | 说明 |
| --- | --- | --- |
| `documentId` | `evidence.documentId` | 资料 ID，用于播放页展示来源 |
| `title` | `documentTitle/title` | 播放页标题 |
| `startTime` | `evidence.startTime` | 必填定位时间，支持 `HH:MM:SS`、`MM:SS` 和带毫秒时间 |
| `endTime` | `evidence.endTime` | 可选结束时间 |
| `sourcePath` | `evidence.sourcePath` 或明确视频 URL 来源 | 来源追踪，页面只在其为明确视频 URL 时自动作为播放器地址 |
| `videoUrl` | 公开视频 URL | 显式播放器地址，支持签名 URL、CDN 转发 URL 或无扩展名播放接口 |

如果 Python 返回的 `playbackUrl` 已经是 `/videos?...`，前端会归一化后复用，并优先保留 evidence 自身的 `startTime/endTime`。如果 `playbackUrl` 是 `https://...mp4#t=10` 这类外部 URL，前端会剥离 fragment 后作为 `videoUrl` 传入 `/videos`，避免 fragment 秒点和 query 秒点冲突。`/videos` 只允许浏览器直接访问的 `http(s)` 播放地址；缺少可播放 URL 但已有时间戳时展示降级提示：“当前 evidence 已定位到时间段，但来源不是浏览器可直接访问的视频 URL，请配置 ALIYUN_OSS_PUBLIC_BASE_URL 或补充签名 URL 服务。”

## 原始视频 RAG 策略

原始视频文件通过普通资料上传入口进入 Java，Java 先保存到 OSS 或本地，再把文件字节转发给 Python。Python 的视频处理链路如下：

```text
mp4/mov/webm/mkv/avi
-> FFmpeg 抽取 16kHz 单声道音频
-> 有公开视频 URL 时优先用百炼 qwen3-asr-flash-filetrans 生成带句级时间戳的 SRT 字幕
-> 本地/离线视频优先读取同目录同名 .srt/.vtt/.txt 侧车字幕或转写文本，作为无 FFmpeg 时的时间戳证据来源
-> 若视频存在内嵌字幕轨，使用 FFmpeg 提取为 SRT 后入库
-> filetrans 不可用时降级 qwen3-asr-flash 同步转写
-> 字幕解析为带 startTime/endTime 的 DocumentBlock
-> FFmpeg 按 `RAG_VIDEO_FRAME_SCAN_MODE` 抽候选帧；`auto/full` 会先严格探测视频时长并按全时长动态间隔抽帧，失败时 `auto` 降级为 `prefix`
-> 阶段 A 扫描完整候选帧，生成 initial_slide、ppt_flip、interval、ambiguous_visual、repeat_visual_confident 和 visual_verification 事件
-> 阶段 B 按全视频时间桶、触发优先级、最小间隔和可选 OCR 上限选择关键帧；默认不设最终 OCR 帧数上限
-> Pillow 缩略图差异检测 PPT 翻页，Pillow dHash/aHash 视觉指纹检测疑似重复画面
-> 百炼 Qwen-OCR / pytesseract 识别关键帧文字
-> 画面 OCR 结果生成 evidenceChannel=frame_ocr 的 DocumentBlock
-> frame_ocr 近重复聚合只基于 OCR 文本相似度，保留代表帧、OCR-confirmed 时间和 duplicateGroupId；未 OCR 的视觉重复时间只进入 visualTimeRanges
-> 结合字幕和画面 OCR 生成 evidenceChannel=video_segment_summary 的视频片段摘要
-> 字幕 evidence、画面 evidence 和片段摘要统一进入 RAG
-> 影响资料可用性的阶段 warning 写入 parseQuality.messages，Java 在 PARTIAL 时记录 RAG_INDEX_PARTIAL 日志并保留 errorLocation
```

依赖和配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `FFMPEG_COMMAND` | conda 环境默认通过 `ffmpeg` 包提供，环境外运行时读取 PATH 中的 `ffmpeg`，未配置时可降级使用 `imageio-ffmpeg` 打包的 ffmpeg | 视频抽音频、抽关键帧和内嵌字幕 |
| `FFPROBE_COMMAND` | conda 环境默认随 `ffmpeg` 包提供，环境外运行时读取 PATH 中的 `ffprobe` | 读取视频时长；不可用时 Python 会尝试用 `ffmpeg -i` 输出解析 `Duration` |
| `RAG_ASR_PROVIDER` | `auto` | `auto/local/dashscope`，生产有 Key 时走百炼 |
| `RAG_ASR_FILETRANS_ENABLED` | `auto` | 有公开视频 URL 时优先启用官方异步时间戳转写 |
| `RAG_ASR_FILETRANS_MODEL` | `qwen3-asr-flash-filetrans` | 百炼异步文件转写模型，返回句级时间戳 |
| `RAG_ASR_MODEL` | `qwen3-asr-flash` | filetrans 失败后的同步 ASR 降级模型 |
| `RAG_ASR_MAX_AUDIO_BYTES` | `10485760` | 同步 ASR 最大音频字节数 |
| `RAG_ASR_FILETRANS_MAX_POLLS` | `30` | 单次请求内等待异步转写结果的最大轮询次数；每次轮询都会写入 `parse.video.asr` 进度 |
| `RAG_ASR_FILETRANS_POLL_INTERVAL_SECONDS` | `2` | 异步转写任务轮询间隔 |
| `RAG_ASR_FILETRANS_MAX_ATTEMPTS` | `2` | filetrans 异步任务提交/轮询失败后的最大尝试次数，超过后才降级到字幕、同步 ASR 或视频元数据 |
| `RAG_VIDEO_AUDIO_SEGMENT_SECONDS` | `300` | 本地或私有长视频同步 ASR 的音频分段时长 |
| `RAG_VIDEO_FFMPEG_TIMEOUT_SECONDS` | `1800` | FFmpeg 抽音频、分段和抽帧超时时间 |
| `RAG_VIDEO_FRAME_SCAN_MODE` | `auto` | `auto/prefix/full`。`prefix` 保持旧版从开头扫描；`full` 严格按视频全时长动态间隔扫描；`auto` 优先 full，失败降级 prefix 并写 warning |
| `RAG_VIDEO_FRAME_SAMPLE_INTERVAL_SECONDS` | `5` | 候选帧采样间隔，用于 PPT 翻页检测 |
| `RAG_VIDEO_FRAME_INTERVAL_SECONDS` | `30` | 固定间隔兜底关键帧间隔 |
| `RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS` | `30` | 阶段 B 最终 OCR 帧之间的最小时间间隔，`initial_slide` 不受淘汰 |
| `RAG_VIDEO_MAX_FRAMES` | 空 | 可选的单个视频最终 OCR 帧上限；未配置或小于 1 时不截断最终 OCR 帧，仍由 `RAG_VIDEO_FRAME_MAX_CANDIDATES`、PPT 翻页检测、视觉去重、最小间隔和每视觉组代表上限控制规模 |
| `RAG_VIDEO_FRAME_TARGET_CANDIDATES` | `360` | full/auto 模式期望候选帧数量，用于计算动态采样间隔 |
| `RAG_VIDEO_FRAME_MAX_CANDIDATES` | `720` | 单个视频最多抽取的候选帧数量；full/auto 会在估算超限时继续放大有效间隔 |
| `RAG_VIDEO_PPT_FLIP_DIFF_THRESHOLD` | `0.08` | 两帧缩略图平均差异超过该值时判定为 PPT 翻页 |
| `RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED` | `true` | 是否在 OCR 前启用视觉指纹重复检测；关闭时回退为 PPT 翻页 + 固定间隔策略 |
| `RAG_VIDEO_FRAME_VISUAL_HASH_ALGORITHM` | `dhash` | 视觉指纹算法，支持 `dhash/ahash`，均由 Pillow 实现 |
| `RAG_VIDEO_FRAME_VISUAL_HASH_MAX_DISTANCE` | `4` | 高置信视觉重复的最大 hash 汉明距离 |
| `RAG_VIDEO_FRAME_VISUAL_SAME_DIFF_THRESHOLD` | `min(0.03, ppt_threshold*0.5)` | 高置信视觉重复的像素差异阈值；未显式配置时按 PPT 阈值保守派生 |
| `RAG_VIDEO_FRAME_VISUAL_AMBIGUOUS_MARGIN` | `2` | hash 距离落在重复阈值附近时标记为 ambiguous_visual，允许进入 OCR |
| `RAG_VIDEO_FRAME_MAX_REPRESENTATIVES_PER_VISUAL_GROUP` | `1` | 同一视觉组默认只选 1 个普通代表帧，ambiguous_visual 和 visual_verification 不受该限制 |
| `RAG_VIDEO_FRAME_VISUAL_VERIFY_INTERVAL_SECONDS` | `900` | 同一视觉组距离上次 OCR 候选超过该间隔时，从重复帧中抽 visual_verification |
| `RAG_VIDEO_FRAME_VISUAL_STAY_VERIFY_SECONDS` | `600` | 同一视觉组连续停留超过该时长时触发验证 OCR |
| `RAG_VIDEO_FRAME_VISUAL_REVISIT_VERIFY_SECONDS` | `1800` | 同一视觉组长时间回跳再次出现时触发验证 OCR |
| `RAG_VIDEO_FRAME_VISUAL_VERIFICATION_RATIO` | `0.25` | `visual_verification` 的默认预算比例；显式配置 `RAG_VIDEO_MAX_FRAMES` 时按该上限计算，未配置时按普通 OCR 候选数量计算 |
| `RAG_VIDEO_FRAME_MAX_VERIFICATIONS_PER_VISUAL_GROUP` | `2` | 单个视觉组默认最多验证 OCR 次数，超限写 skipped warning |
| `RAG_VIDEO_SEGMENT_SECONDS` | `120` | 视频片段摘要的默认时间窗口 |
| `RAG_VIDEO_SEGMENT_MAX_CUES` | `6` | 单个视频片段摘要最多合并的字幕 cue 数 |
| `RAG_VIDEO_OCR_DEDUP_ENABLED` | `true` | 是否在入库前合并视频关键帧 OCR 近重复 evidence |
| `RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD` | `0.86` | 视频 OCR 文本近似去重的 Jaccard 相似度阈值 |
| `RAG_VIDEO_OCR_DEDUP_MAX_GAP_SECONDS` | `180` | 同一近重复组内相邻帧允许的最大时间间隔 |
| `RAG_VIDEO_OCR_DEDUP_MIN_TEXT_CHARS` | `30` | 低于该长度的 OCR 文本不做近似合并，只允许同范围内完全 hash 合并 |
| `RAG_QUERY_DIVERSITY_DEDUP_ENABLED` | `true` | 查询阶段是否对 rerank 后 evidence 做多样性过滤 |
| `RAG_QUERY_VIDEO_TIME_WINDOW_SECONDS` | `120` | 查询阶段按视频时间窗限制近重复 evidence 的窗口长度 |
| `RAG_QUERY_VIDEO_MAX_PER_TIME_WINDOW` | `1` | 同一视频同一时间窗内每类视频 evidence 默认保留数量 |
| `BAILIAN_OCR_MAX_ATTEMPTS` | `3` | 单张图片或视频关键帧调用百炼 OCR 的总尝试次数，建议生产按稳定性调到 `3-5` |
| `BAILIAN_OCR_RETRY_DELAY_SECONDS` | `2` | OCR 单次失败后的重试等待秒数 |

如果 FFmpeg、ASR、PPT 翻页检测、OCR 或片段摘要任一环节不可用，Python 会尽量降级保留已生成 evidence；完全没有字幕和画面 OCR 时，只保留 `evidenceChannel=video_metadata` 的视频元数据 evidence，不再用元数据占位块生成假的视频片段摘要，并返回 `PARTIAL`。同步 ASR 降级路径不保证模型一定返回真实时间戳；若只得到纯文本，Python 会按视频时长生成估算 SRT 时间段作为播放定位保底。生产视频资料如已提供侧车字幕或内嵌字幕，Python 不再额外等待 filetrans；缺少字幕时仍建议配置公开 OSS/CDN URL，让 filetrans 返回可验证的句级时间戳。影响资料可用性的阶段告警会进入 `parseQuality.messages`，Java 记录 `RAG_INDEX_PARTIAL`，日志上下文的 `errorLocation` 可直接定位到具体环节。

### 视频 OCR 近重复治理

视频课程、代码讲解和 PPT 录屏会在固定间隔抽帧时产生大量相似画面。V6 分为 OCR 前视觉重复验证和 OCR 后文本去重两层：

- OCR 前视觉重复只决定“是否需要进入 OCR 候选”，不会直接生成 `duplicateGroupId`，也不会把未 OCR 时间写入 `timeRanges/sourceFrameTimes`。
- 视觉四态：`new_visual` 正常进入 OCR；`ambiguous_visual` 必须允许进入 OCR 且 OCR 前不复用 `detectedSlideIndex`；`repeat_visual_confident` 默认只写 `visualTimeRanges/visualSourceFrameTimes`；`visual_verification` 是从高置信重复帧中抽出的少量验证 OCR，防止小数字、小文字变化漏检。
- 高置信视觉重复必须同时满足保守 hash 距离和低 `image_difference_score`。只满足其中一项时最多视为 ambiguous，不能直接跳过 OCR。
- `visual_verification` 触发条件包括距离该视觉组上次 OCR 候选超过验证间隔、重复帧落在阶段 B 尚无代表覆盖的时间桶、同一画面长时间停留、长间隔回跳再次出现。命中全局预算或 per-group cap 时，`parseQuality.messages` 写入 `visualVerificationSkippedCount`、`visualVerificationSkippedRanges`、`visualVerificationBudget` 和 `visualVerificationPerGroupLimit`。

Python 在入库前对 `evidenceChannel=frame_ocr` 的画面 OCR 块做保守聚合：

- 只处理画面 OCR，不合并字幕 cue。
- OCR 文本先去掉“视频画面 HH:MM:SS”标题、代码围栏和明显空白噪声，但保留代码 token 与技术关键词。
- 近似合并必须同时满足同一资料、同一 evidence 通道、同一 `detectedSlideIndex/slideIndex`、同一 `visualGroupId` 或无页码时同一时间窗、文本相似度不低于 `RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD`。`visualGroupId` 只能扩展长间隔匹配范围，不能绕过 OCR 文本相似度。
- 低于 `RAG_VIDEO_OCR_DEDUP_MIN_TEXT_CHARS` 的短文本跳过近似合并；即使完全 hash 相同，也必须满足同一页或同一时间窗和最大时间间隔，避免跨章节把“目录”“总结”等短标签误合并。
- 代表帧优先选择 `ppt_flip`，其次 `initial_slide/ambiguous_visual/visual_verification`，再是固定间隔帧；同优先级下比较置信度、文本长度和更早时间。
- `duplicateGroupId` 继续基于 OCR normalized text hash + 时间桶生成，不改为 `visualGroupId`。

关键 metadata 字段契约：

- `timeRanges/sourceFrameTimes` 只表示该 `DocumentBlock` 的 OCR 文本已确认适用于这些时间点。只有被 OCR 的帧，或 OCR 后文本相似合并确认的帧，才能进入这里。
- `visualTimeRanges/visualSourceFrameTimes` 只表示视觉上疑似同一画面但未 OCR 文本确认的时间点，不参与 `frames_between` 的文本画面线索匹配。
- `suspectedVisualGroupId` 只表示候选视觉归属，不作为 `detectedSlideIndex`、`duplicateGroupId`，也不直接驱动文本合并。
- `visualGroupId` 可作为 OCR 后去重的 scope 候选，但不能绕过 OCR 文本相似度。
- 代表块 `contentText` 里的“重复出现时间”只能写 OCR-confirmed 的 `sourceFrameTimes`，不能写 visual-only 时间。

聚合后的画面 evidence 保留：

```json
{
  "evidenceChannel": "frame_ocr",
  "duplicateGroupId": "material-11-frame-ocr-ab12cd34ef56-4",
  "contentHash": "sha256...",
  "normalizedTextHash": "sha256...",
  "representativeTime": "00:08:30",
  "timeRanges": [
    {"startTime": "00:06:00", "endTime": "00:06:00"},
    {"startTime": "00:08:30", "endTime": "00:08:30"},
    {"startTime": "00:09:00", "endTime": "00:09:00"}
  ],
  "sourceFrameTimes": ["00:06:00", "00:08:30", "00:09:00"],
  "visualGroupId": "visual-0004",
  "visualTimeRanges": [
    {"startTime": "00:10:30", "endTime": "00:10:30"}
  ],
  "visualSourceFrameTimes": ["00:10:30"],
  "mergedFrameCount": 3,
  "dedupStrategy": "video_frame_ocr_text_jaccard"
}
```

`startTime` 使用最早 OCR-confirmed 出现时间，`endTime` 使用最后 OCR-confirmed 出现时间；现阶段播放定位仍默认跳到 `startTime`。`representativeTime` 仅说明哪一帧作为文本和向量化代表。视频片段摘要会根据 `timeRanges` 判断画面与字幕窗口是否相交，并在 `video_segment_summary.metadata.frameDuplicateGroupIds` 中记录关联的画面重复组。`frames_between` 默认不看 `visualTimeRanges`；当同一片段匹配超过 3 个画面帧时，按片段中心距离、触发优先级、文本长度和置信度排序后取前 3 个。

查询阶段会在 RAG-Fusion 和 rerank 后再执行多样性过滤：同一 `duplicateGroupId` 的重复 `frame_ocr` 不会重复进入最终 topK；同一视频同一时间窗默认最多保留 1 条 `frame_ocr` 和 1 条 `video_segment_summary`，不足 topK 时再按 rerank 顺序补足。查询多样性继续依赖 `duplicateGroupId` 和时间窗，不看 `visualGroupId`。旧数据不会自动获得入库前去重效果，需要对资料执行“重建索引”后生效；查询后过滤仍可缓解旧数据的重复返回。

## 百炼 LLM 回答与 rerank

查询阶段在 RAG-Fusion 后增加后检索重排，再进入回答生成：

```text
Multi-Query -> BM25 + 向量召回 -> RRF/RAG-Fusion -> 百炼 qwen3-rerank -> 百炼 qwen-plus 生成带引用回答
```

配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `RAG_RERANK_PROVIDER` | `auto` | `auto/local/dashscope` |
| `RAG_RERANK_MODEL` | `qwen3-rerank` | 百炼重排模型 |
| `RAG_LLM_PROVIDER` / `RAG_ANSWER_PROVIDER` | `auto` | `auto/local/dashscope`，当前实现读取 `RAG_ANSWER_PROVIDER` |
| `RAG_LLM_MODEL` | `qwen-plus` | 百炼回答生成模型 |
| `RAG_LLM_TEMPERATURE` | `0.2` | 回答生成温度 |

无 evidence 时直接拒答并提示上传资料；有 evidence 时，Prompt 要求回答只能基于 evidence，且关键结论保留 `[evidenceId]` 引用。回答生成后会程序化追加“证据引用”摘要，包含 evidenceId、资料标题、章节或视频时间、来源和分数，避免完全依赖模型自觉保留引用字段。章节位置需要先清洗 Markdown 链接和加粗标记，只保留可读标题文本；如果 evidence 的 `sourcePath` 是可由浏览器直接访问的 `http(s)` OSS/CDN URL，则位置应渲染为新标签页链接，链接目标为 `sourcePath`，并尽量拼接原 Markdown 目录中的 `#...` fragment。裸 `#...` 或当前 React 应用根路径 hash 不能作为目标，应映射到来源文件 URL 后再展示。测试环境默认走本地确定性回答和重排，避免消耗百炼额度。

## 多格式解析策略

| 格式 | 原生优先策略 | 补充策略 |
| --- | --- | --- |
| `pdf` | MinerU；失败后 PyMuPDF/pdfplumber/pypdf 可用方案 | OCR 可用时补充图片型页面 |
| `docx` | `python-docx` 提取标题、段落、表格、图片 | 低置信或高精度时 LibreOffice 转 PDF 后 MinerU/OCR |
| `doc` | LibreOffice headless 转 `docx` 和 `pdf` | 分别走 DOCX 原生解析与 PDF/MinerU |
| `pptx` | `python-pptx` 提取幻灯片标题、文本框、表格、图片、备注；依赖缺失或原生解析异常时使用标准库读取 PPTX XML 文本 | 低置信或高精度时渲染 PDF/图片后 MinerU/OCR；XML 降级文本充足时仍可返回 `READY` |
| `ppt` | LibreOffice headless 转 `pptx` 和 `pdf` | 分别走 PPTX 原生解析与 PDF/MinerU |
| `md` | Markdown AST parser，保留标题、段落、列表、表格、代码块、图片链接 | AST 依赖不可用时退回结构化行解析 |
| `xlsx/xls` | `openpyxl/pandas` 解析 sheet、区域、坐标、公式、合并单元格 | 无文本区域时返回低置信 `PARTIAL/FAILED` |
| `png/jpg/jpeg/webp` | OCR | 预留图片摘要字段，不在 Java 中生成摘要 |
| `txt` | 编码探测后直接文本解析 | 解码失败时返回 `FAILED` |
| `srt/vtt` | 解析字幕 cue，保留开始和结束时间 | 作为 `mediaType=video`、`evidenceChannel=subtitle` 的时间戳证据入库 |
| `mp4/mov/m4v/webm/mkv/avi` | 侧车字幕/内嵌字幕优先；缺字幕时 FFmpeg + 百炼 ASR；随后候选帧采样 + PPT 翻页检测 + 关键帧 OCR + 视频片段摘要 | 失败时返回视频元数据 evidence，`parseQuality.messages` 标出阶段位置，并标记 `PARTIAL` |

## 解析质量判断

Python 对 `docx/pptx/xlsx` 等原生解析结果生成质量指标：

- 原生解析文本字符数。
- 段落数量。
- 表格数量。
- 图片数量。
- shape/textbox/drawing 数量。
- 嵌入对象数量。
- 合并单元格数量。
- 空单元格比例。
- 是否疑似截图型文档。
- 用户是否选择高精度解析。

当质量低、疑似截图型或用户选择高精度解析时，Python 补跑 PDF + MinerU/OCR。补跑失败但原生块可用时返回 `PARTIAL`。

## RAG 入库流程

```text
原始文件
-> 解析路由
-> DocumentBlock
-> 内容清洗（删除 PostgreSQL 不支持的 NUL/0x00 字符，覆盖正文、章节名、来源路径和 metadata）
-> 递归切块
-> 父段 metadata 构建与 OCR occurrence 展开
-> 文档/章节摘要 + parent summary child
-> BM25 索引
-> Embedding 向量索引
-> 事务写入 rag_document + rag_chunk
-> 事务内和提交后切块数校验
```

切块规则：

- 文本块按标题、章节、页面、幻灯片、段落、句子递归切分。
- 表格、图片、代码块、公式和图表默认作为原子块保存，避免随意切碎。
- chunk metadata 保留 `blockId/blockType/pageIndex/slideIndex/sheetName/cellRange/startTime/endTime/sectionTitle/sourcePath/assetPath/parseEngine`，并保留父子索引字段 `parentSegmentId/parentStartTime/parentEndTime/parentKind/childKind/occurrenceId/occurrenceTime/retrievalLayer/concepts/segmentRole/prerequisiteSegmentIds/relatedSegmentIds/matchedChildIds/matchedChildKinds/linkedVisualGroupIds/linkedDuplicateGroupIds`。

## 简历模板字段级内容补丁接口

更新日期：2026-06-24

本节描述 RAG 服务边界内的简历模板、字段补丁和 DOCX 确定性导出能力。用户填写岗位 JD、发起简历修改和审批修改建议由 Agent 工作台承载；`/resume-template` 前端页面只负责模板上传、历史模板选择、图片预览和区域确认，不展示 Agent/Python 提取出来的字段原文、定位、样式指纹或 layout fingerprint。Structured Outputs / JSON Schema 只用于约束 LLM 输出字段级补丁；DOCX 排版变化由 Python 确定性应用、人工确认、`LayoutChangeContract` 和 layout fingerprint 差异审计共同控制。

### 状态机和错误码

模板状态：

| 状态 | 含义 |
| --- | --- |
| `PARSING` | Java 已保存原文件，Python 正在解析字段 |
| `READY` | 字段绑定可用 |
| `FAILED` | 解析失败 |
| `EXPORTED` | 已导出至少一个新版本 |

补丁草稿状态：

| 状态 | 含义 |
| --- | --- |
| `DRAFT` | Python 生成的待确认草稿 |
| `VALIDATED` | 已通过 Java/Python 双重校验 |
| `CONFIRMED` | 用户确认可应用 |
| `REJECTED` | 用户拒绝 |
| `EXPORTED` | 已被导出使用 |

错误码：

| 错误码 | HTTP/Result 行为 | 说明 |
| --- | --- | --- |
| `RESUME_TEMPLATE_NOT_FOUND` | `Result.error` | 模板不存在或不属于当前用户 |
| `RESUME_TEMPLATE_VERSION_CONFLICT` | `Result.error` | 前端提交版本不是当前版本 |
| `RESUME_PATCH_VALIDATION_FAILED` | `Result.error` | `fieldId/hash/evidenceIds/长度/行数/注入风险` 校验失败 |
| `RESUME_EXPORT_REQUIRES_CONFIRMATION` | `Result.error` | 存在未确认补丁时尝试导出 |
| `RESUME_LAYOUT_CHANGED` | `Result.error` | Python 应用后出现未被 `LayoutChangeContract` 授权的 layout fingerprint 变化 |
| `RAG_PYTHON_4XX/RAG_PYTHON_5XX/RAG_PYTHON_TIMEOUT` | `Result.error` | Java 调 Python 内部接口失败 |

### LayoutChangeContract

简历改写不再把“结构变化”一律视为失败，而是区分是否经过用户授权。Java 调 Python 校验或导出时可携带 `layoutContract`：

```json
{
  "mode": "PRESERVE_LAYOUT",
  "allowedChanges": [],
  "maxPageDelta": 0,
  "maxParagraphDelta": 0,
  "maxRunDelta": 0,
  "requireVisualCheck": true
}
```

| 模式 | 含义 | 检查规则 |
| --- | --- | --- |
| `PRESERVE_LAYOUT` | 默认保排版，只允许字段文本变化 | `paragraphCount/tableCount/runCount/mediaNames/relationshipHashes/structureHash` 必须完全一致 |
| `CONTROLLED_EDIT` | 用户明确要求新增段落、删除段落、加粗关键词等受控变化 | 变化必须落在 `allowedChanges` 白名单内，且段落/run 变化不超过阈值 |
| `RELAYOUT` | 用户要求大幅重排版或换结构 | Python 只返回需要人工预览确认的报告，Java 不应静默保存为正式版本 |

`allowedChanges[]` 当前预留 `TEXT_REPLACE`、`STYLE_RANGE`、`INSERT_PARAGRAPH`、`DELETE_PARAGRAPH` 四类。Agent 只能根据用户明确要求生成契约候选，不能自行放宽 `mode` 或阈值。没有契约时一律按 `PRESERVE_LAYOUT` 处理。

### Java 对外 API

#### 查询简历模板历史

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/resume-templates?limit=12` |
| 鉴权 | 必须携带 `Authorization: Bearer <token>` |
| 响应 | `Result<List<ResumeTemplateVO>>` |

用途：模板页和 Agent 工作台都通过该接口展示当前用户已上传或导出的 DOCX 模板历史。前端选择历史模板后必须再调用 `GET /api/rag/resume-templates/{templateId}` 和预览接口刷新当前版本摘要、图片和区域约束；接口不返回字段原文、字段 hash、内部定位或样式指纹。

#### 上传并解析简历模板

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/resume-templates` |
| 鉴权 | 必须携带 `Authorization: Bearer <token>` |
| 请求类型 | `multipart/form-data`，字段 `file` |
| 文件限制 | 首版只支持 `.docx`，建议不超过 10MB |
| 响应 | `Result<ResumeTemplateVO>` |

成功响应：

```json
{
  "code": 1,
  "data": {
    "templateId": "d2f0...",
    "version": 1,
    "status": "READY",
    "filename": "后端实习简历.docx",
    "fieldCount": 8,
    "unsupportedRegionCount": 0,
    "createdAt": "2026-06-22T10:00:00"
  }
}
```

#### 查看模板摘要

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/resume-templates/{templateId}` |
| 响应 | `Result<ResumeTemplateVO>` |

Java 必须按当前登录用户和 `templateId` 查询，不能返回其他用户模板。对外 `ResumeTemplateVO` 只返回 `fieldCount/unsupportedRegionCount` 等摘要，不返回字段原文、字段 hash、locationRefs、styleFingerprint 或 layoutFingerprint；前端页面不展示 `fieldCount`，只把模板作为可选择资源使用。Java 生成补丁、预览和导出时仍可在服务端读取字段绑定并转发给 Python 内部接口。

#### 删除简历模板

| 项目 | 内容 |
| --- | --- |
| 方法 | `DELETE` |
| 路径 | `/api/rag/resume-templates/{templateId}` |
| 响应 | `Result<Void>` |

Java 先按当前登录用户校验模板归属，再删除主记录。由于数据库表已配置级联删除，字段、预览页、区域标注、补丁草稿和导出记录会一并清理；服务层还会尽力删除原始 DOCX、预览图片和导出文件等私有对象存储文件。删除失败时以日志告警为主，不回滚已完成的数据库删除。

#### 生成字段补丁草稿

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/resume-templates/{templateId}/patches/generate` |
| 请求 | `ResumePatchGenerateDTO` |
| 响应 | `Result<ResumePatchDraftVO>` |

请求：

```json
{
  "version": 1,
  "jobDescription": "岗位 JD 文本",
  "resumeText": "从用户已上传简历资料读取到的解析摘要",
  "resumeMaterialId": 18,
  "resumeMaterialTitle": "王同学-后端实习简历.pdf",
  "topK": 5,
  "useConfirmedAnnotations": false
}
```

Java 行为：

1. 校验模板归属和版本。
2. 若传入 `resumeMaterialId`，使用当前用户该资料的服务端 `documentSummary` 作为权威简历摘要；资料不存在、无权访问或摘要为空时拒绝生成。
3. 使用岗位 JD 和简历摘要检索当前用户 RAG evidence 候选，不写入 `rag_query_history`。
4. 未开启 `useConfirmedAnnotations` 时沿用全部安全字段；开启后只使用 `editable=true`、`status=ACTIVE` 且绑定 `fieldId` 的确认区域。
5. 调 Python `/internal/rag/resume/templates/patches/generate`，传入 JD、简历摘要、字段和 evidence。
6. 保存补丁草稿、生成时冻结的 `allowedFieldIds` 和最大 `annotationRevision`，状态为 `DRAFT` 或 `VALIDATED`。
7. 不记录简历全文、JD 全文和模型原始补丁到日志。

前端职责：岗位 JD 不在 `/resume-template` 页面输入；Agent 工作台先选择已上传简历资料读取摘要，再选择模板并提交 JD，之后触发字段补丁生成、人工确认和导出闭环。模板页保存的图片区域约束是 Agent 生成补丁时的字段范围约束，但前端不展示模板字段原文和内部定位信息。

#### 图片预览与区域标注

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/resume-templates/{templateId}/preview?refresh=false` |
| 响应 | `Result<ResumeTemplatePreviewVO>` |

Java 校验模板归属后读取当前受控 DOCX。若已有缓存且未指定 `refresh=true`，直接返回缓存页面和标注；否则调用 Python `/internal/rag/resume/templates/preview`，保存页面图片为私有文件或对象资源，并幂等写入 AUTO 标注。接口不返回公开 `publicUrl`，页面图片通过以下鉴权接口读取：

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/resume-templates/{templateId}/preview/pages/{pageIndex}/image` |
| 响应 | `image/png` 字节流 |

预览响应包含：

- `previewStatus`：`READY`、`PARTIAL` 或 `UNAVAILABLE`。`PARTIAL` 包括 LibreOffice/soffice 不可用时由 Python 生成的字段草图预览；该图片用于确认字段边界和 Agent 可修改范围，不代表 DOCX 精确版式。
- `pages[]`：`pageIndex`、`width`、`height`、`imageUrl`。
- `annotations[]`：`annotationId`、`fieldId`、`pageIndex`、`rect{x,y,width,height}`、`sourceType`、`editable`、`sectionKey`、`userInstruction`、`requiredEvidencePolicy`、`status`、`annotationRevision`。
- `unmappedFields[]`：未映射图片坐标的字段。
- `warnings[]`：LibreOffice/PyMuPDF 不可用、转换失败、字段草图降级或字段未映射等可恢复说明。只有 PyMuPDF 也不可用或 DOCX 内容无法解析时才返回 `UNAVAILABLE`。

保存标注：

| 项目 | 内容 |
| --- | --- |
| 方法 | `PUT` |
| 路径 | `/api/rag/resume-templates/{templateId}/annotations` |
| 请求 | `ResumeTemplateAnnotationSaveDTO` |
| 响应 | `Result<ResumeTemplatePreviewVO>` |

服务端规则：

1. `fieldId` 非空时必须属于当前用户、当前模板、当前版本。
2. `rect` 为 0..1 相对坐标，宽高必须大于 0。
3. evidence policy 只能升级，不能低于字段默认 `requiredEvidencePolicy`。
4. `MANUAL_UNBOUND` 强制 `editable=false`；只有 `AUTO` 或 `MANUAL_BOUND` 且绑定字段后可 `editable=true`。
5. `userInstruction` 最长 500 字，禁止包含定位、排版、XML、路径、字段名等指令。
6. 标注坐标只用于视觉展示和用户约束，不传给 LLM，也不参与 DOCX 定位。

前端确认语义：用户勾选“允许 Agent 修改该字段”只是本地待保存状态；必须调用 `PUT /annotations` 成功后，Agent 侧 `useConfirmedAnnotations=true` 才会读取这些区域约束。模板页需要显式展示“待保存/已保存”和允许修改区域数量，避免用户误认为只改角标就是保存成功。

#### 校验用户选择的补丁

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/resume-templates/{templateId}/patches/validate` |
| 请求 | `ResumePatchValidateDTO` |
| 响应 | `Result<ResumePatchDraftVO>` |

请求：

```json
{
  "version": 1,
  "patches": [
    {
      "fieldId": "p-7aa1c3d091",
      "sourceTextHash": "sha256...",
      "newText": "基于 RAG-Fusion 和 FastAPI 构建学习证据检索服务...",
      "rewriteReason": "突出 JD 中的 RAG 和后端接口能力",
      "evidenceIds": ["material-7-chunk-3"],
      "confidence": 0.82,
      "riskFlags": ["NONE"],
      "status": "CONFIRMED"
    }
  ]
}
```

可选 `layoutContract` 用于用户明确要求受控排版变化时授权；未传时按 `PRESERVE_LAYOUT`。

Java 和 Python 都必须校验：

- `templateId + version + fieldId + sourceTextHash` 匹配。
- `newText` 不超过字段 `maxChars/maxLines`。
- `evidenceIds` 属于本次候选集合。
- `riskFlags/status` 为枚举值。
- 不包含 `style/font/layout/xml/path/locationRefs/run/paragraph/table/cell` 等排版字段。
- 不包含 Markdown 表格、HTML 或 DOCX XML。
- 如用户要求加粗、增加段落或删除段落，不能把排版字段混进 `ResumeContentPatch`，必须通过独立 `layoutContract.allowedChanges` 授权并由 Python 差异审计。

#### 导出确认后的新版本

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/resume-templates/{templateId}/exports` |
| 请求 | `ResumeTemplateExportDTO` |
| 响应 | `Result<ResumeTemplateExportVO>` |

请求：

```json
{
  "version": 1,
  "patchDraftId": "draft-uuid",
  "idempotencyKey": "resume-export-20260622-001",
  "layoutContract": {
    "mode": "PRESERVE_LAYOUT",
    "allowedChanges": []
  }
}
```

导出约束：

- `patchDraftId` 必须属于当前用户模板。
- 所有将应用的补丁必须是 `CONFIRMED` 或 `VALIDATED`。
- 幂等键重复时返回已有导出记录，不重复生成。
- Python 返回 `RESUME_LAYOUT_CHANGED` 时，Java 不保存导出文件。
- 未传 `layoutContract` 时按 `PRESERVE_LAYOUT`，任何段落数、run 数、关系、媒体或结构 hash 变化都会拒绝保存。
- 导出保存为新对象，不覆盖原始模板。
- 静态图片、头像和 Logo 等 `w:drawing/w:pict` 结构不再作为全局导出拒绝条件；它们仍保留在 layout fingerprint 的媒体文件和 relationship 校验中。若图片所在段落或文本框字段本身被识别为不可改写字段，字段级校验仍会拒绝自动修改。
- 超链接、域代码、批注、修订痕迹、脚注尾注、文本框、内容控件等复杂结构仍会触发导出前拒绝，避免确定性替换误改结构化内容。

### Python 内部 API

Python 内部接口只服务 Java，不能接收任意本地文件路径。

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| `POST` | `/internal/rag/query` | `QueryRequest` | `QueryResponse`，包含回答准入字段 |
| `POST` | `/internal/rag/query/tasks` | `QueryRequest` | `QueryTaskResponse`，任务完成后 `result` 包含回答准入字段 |
| `GET` | `/internal/rag/query/tasks/{taskId}` | path `taskId` | `QueryTaskResponse` |
| `POST` | `/internal/rag/resume/templates/parse` | multipart DOCX 字节、`template_id/version` | `ResumeTemplateParseResponse` |
| `POST` | `/internal/rag/resume/templates/patches/generate` | `ResumePatchGenerationRequest` | `ResumePatchGenerationResponse` |
| `POST` | `/internal/rag/resume/templates/patches/validate` | `ResumePatchValidationRequest` | `ResumePatchValidationResponse` |
| `POST` | `/internal/rag/resume/templates/exports` | base64 DOCX、字段绑定、已确认补丁 | `ResumeTemplateExportResponse` |

超时和重试：

| 调用 | 超时 | 重试 |
| --- | --- | --- |
| Java -> Python query | 60 秒 | 网络错误可重试 1 次；`answerStatus=REFUSED` 不是错误，不触发重试 |
| Java -> Python query task | 创建 10 秒，轮询 10 秒 | 创建失败按下游错误处理；轮询到 `COMPLETED/REFUSED` 均按成功业务结果处理 |
| Java -> Python parse | 30 秒 | 网络错误可重试 1 次 |
| Java -> Python patch generate | 60 秒 | 只对网络错误重试 1 次，模型 schema 失败不盲目重试超过 2 次 |
| Java -> Python validate | 10 秒 | 不重试业务校验失败 |
| Java -> Python export | 30 秒 | 幂等键保障下网络错误可重试 1 次 |

### Structured Outputs 约束

OpenAI provider 可用时，Python 使用 Chat Completions `response_format.type=json_schema`，在 `json_schema` 中设置 `strict:true`，并为所有 object 设置 `additionalProperties:false`。百炼 OpenAI 兼容路径当前不声明同等强保证，只作为 JSON 生成路径，必须经过 Pydantic/JSON Schema validation、必要 retry 和 reject fallback。

字段补丁 schema 根对象：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["patches"],
  "properties": {
    "patches": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "fieldId",
          "sourceTextHash",
          "newText",
          "rewriteReason",
          "evidenceIds",
          "confidence",
          "riskFlags",
          "status"
        ]
      }
    }
  }
}
```

## 前端影响

前端保持后台管理风格，只补充必要字段：

- 上传支持格式文案扩展，包含 `.srt/.vtt` 字幕和带时间戳的 `.txt` 转写文本。
- 工作台 `/api/page-data/dashboard` 支持 `startDate`、`endDate`、`recentDays` 和 `recentLimit` 查询参数；新前端使用 `startDate/endDate` 做“从/到”日期范围筛选，用户点击“确定”后才触发后端查询，范围限制在最近 7 天内，`recentDays` 仅作为旧调用兜底，`recentLimit` 默认 5 条、最多 50 条。
- `DashboardVO` 会返回 `recentTaskStartDate`、`recentTaskEndDate` 和 `recentTaskLimit`，用于前端展示后端实际生效的任务查询范围；后端通过 `learning_material.updated_at >= startDate 00:00:00` 且 `< endDate + 1 day 00:00:00` 查询，已用单测校验 mapper 收到真实起止时间。
- 工作台 RAG 快速检索区通过 `/api/rag/query/history` 展示“近期询问记录”，用户可选择最近 7 天内的从/到日期和条数，点击历史记录后回填该次回答、证据、问题和阶段事件快照。
- 上传后的顶部栏、工作台上传区和资料页上传区不再只显示“已上传，正在后台解析”，而是轮询单个资料状态并显示类似“第 133/173 块：生成 embedding · 切块 133/173 · 77%”的主进度。
- 资料列表显示 `PENDING/PARSING/READY/PARTIAL/FAILED/REINDEXING`。
- 点击刷新时，如果接口短暂没有返回 `latestProgress`，前端保留该资料已有进度，避免大文件解析过程中进度块闪烁或消失；后端返回新的 `latestProgress` 时立即覆盖旧进度。
- 上传资料卡片提供“重建索引”和“高精度补跑”入口；高精度补跑会调用 `/api/rag/materials/{id}/reindex?highPrecision=true`。
- evidence 卡片展示页码、幻灯片、sheet、cell range、视频时间段、播放定位入口、解析器和检索来源。视频 evidence 的位置链接和播放定位入口都使用 React Router 内部跳转到 `/videos?documentId=...&title=...&startTime=...&endTime=...&sourcePath=...&videoUrl=...&returnTo=...`，播放器加载后跳到 `startTime`，到达 `endTime` 后在视频画面上弹出提示并暂停一次，用户可点击“继续播放”恢复视频，也可点击“返回检索页”回到进入播放页前的检索页面；非视频 evidence 不展示播放按钮。
- 非视频 Markdown/text evidence 的位置链接和回答末尾“证据引用”中的来源链接打开 `/preview/material/{id}` 新标签页，由前端预览页调用 `/api/rag/materials/{id}/preview` 展示内容，避免直接访问 OSS URL 触发下载。视频 evidence 的回答链接会按 evidenceId 或同一行的 `时间=开始-结束` 匹配到对应 `/videos` 播放链接，避免直接打开 OSS 视频文件触发下载。
- 知识库页、工作台快速检索和近期询问记录都需要展示 `answerStatus`。`REFUSED` 时使用 `refusalMessage` 或 `answer` 显示“证据不足”状态，不展示空 evidence 为异常，不触发前端错误态。
- 知识库页和工作台 RAG 快速检索区必须把 `RagQueryVO.answer` 按安全 Markdown 子集渲染，支持标题、段落、列表、引用、代码、链接、加粗、行内公式和 `[evidenceId=...]` 标记，禁止使用未净化 HTML 注入。渲染回答时需要结合返回的 `evidences` 把可预览或可播放的资料 URL 改写为应用内预览页或视频播放器链接。
- RAG 查询提交后立即展示 `query.expand -> query.filter -> query.bm25 -> query.vector -> query.fusion -> query.rerank -> query.guard -> query.answer` 阶段面板；响应返回后使用 `RagQueryVO.progressEvents` 中的真实阶段、百分比、模型事件和完成/失败状态覆盖前端占位状态。
- Agent 非持久化探针结果需要返回 `answerStatus/refusalReason/confidence`；工具结果中的 evidenceIds 只取 `supportingEvidenceIds`，拒答时为空。

## 阿里 OSS 上传配置

生产环境上传文件建议进入阿里 OSS，Java 仍会把文件字节转发给 Python 完成当前请求的解析入库，Python 不直接持有 OSS 密钥。

| 配置项 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `evidence.storage.provider` | `EVIDENCE_STORAGE_PROVIDER` | `local` | `local` 写本地；`oss` 上传阿里 OSS |
| `evidence.storage.local-root` | `EVIDENCE_UPLOAD_ROOT` | `uploads/rag` | 本地模式保存目录 |
| `evidence.storage.oss.endpoint` | `ALIYUN_OSS_ENDPOINT` | 空 | OSS Endpoint，例如 `https://oss-cn-hangzhou.aliyuncs.com` |
| `evidence.storage.oss.bucket` | `ALIYUN_OSS_BUCKET` | 空 | OSS Bucket 名称 |
| `evidence.storage.oss.access-key-id` | `ALIYUN_OSS_ACCESS_KEY_ID` | 空 | OSS AccessKey ID |
| `evidence.storage.oss.access-key-secret` | `ALIYUN_OSS_ACCESS_KEY_SECRET` | 空 | OSS AccessKey Secret |
| `evidence.storage.oss.object-prefix` | `ALIYUN_OSS_OBJECT_PREFIX` | `learning-evidence` | OSS 对象 key 前缀 |
| `evidence.storage.oss.public-base-url` | `ALIYUN_OSS_PUBLIC_BASE_URL` | 空 | 可选公开访问域名或 CDN 域名，用于视频播放和 evidence 跳转 |

OSS 模式写入 `learning_material.original_file_path` 的优先级：

1. 已配置 `public-base-url`：保存公开 URL，例如 `https://cdn.example.com/learning-evidence/.../file.mp4`。
2. 未配置公开域名：保存 `oss://bucket/objectKey`，只用于来源追踪，浏览器不能直接播放私有对象。

## 百炼 OCR 接入

更新日期：2026-06-16

本阶段只把 OCR 模型作为 Python RAG 文档解析降级链路的一部分，不新增 Agent 编排、工具调用或长任务调度。Java 仍只上传文件、记录状态并调用 Python；百炼 OCR 调用统一使用 `DASHSCOPE_API_KEY`，模型选择、超时和失败降级全部位于 `ai-python/`。

### 调用位置

| 输入场景 | Python 行为 | 失败处理 |
| --- | --- | --- |
| `png/jpg/jpeg/webp` 图片文件 | 优先调用百炼 Qwen-OCR，输出 `DocumentBlock(blockType=image)` | 百炼未配置或调用失败时降级 `pytesseract`；仍失败则返回低置信图片块并进入 `PARTIAL/FAILED` 判定 |
| PDF 本地文本提取为空 | 将页面渲染为图片后逐页调用百炼 Qwen-OCR | 单页失败则继续后续页面；百炼失败后再降级本地 `pytesseract` |
| DOCX/PPTX 高精度或低质量补充 | LibreOffice 转 PDF 后复用 PDF 解析链路 | 转换或 OCR 失败但原生块可用时返回 `PARTIAL` |

依赖边界：

| 依赖 | 来源 | 说明 |
| --- | --- | --- |
| `tesseract` | `ai-python/environment.yml` | 本地 `pytesseract` 降级 OCR 调用的可执行程序 |
| Tesseract 语言数据 | 本机 Tesseract 数据目录 | `OCR_LANG=chi_sim+eng` 需要 `chi_sim` 和 `eng` traineddata；缺失时会报语言包错误 |
| `MinerU` | 外部安装并通过 `MINERU_COMMAND` 接入 | PDF 高精度识别优先使用；未配置或失败时走本地降级解析 |
| `LibreOffice` / `soffice` | 外部安装并通过 `LIBREOFFICE_COMMAND` / `SOFFICE_COMMAND` 接入 | `.doc/.ppt` 转结构化格式或 PDF，DOCX/PPTX 低置信补跑时使用 |

### 百炼请求配置

使用阿里云百炼 / DashScope OpenAI 兼容接口。官方参考：

- `https://help.aliyun.com/zh/model-studio/qwen-vl-ocr-api-reference`
- `https://www.alibabacloud.com/help/en/model-studio/qwen-vl-ocr`

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BAILIAN_OCR_ENABLED` | `auto` | `auto` 表示存在 Key 时启用；`true/1/yes` 强制启用；`false/0/no` 禁用 |
| `DASHSCOPE_API_KEY` | 空 | 百炼平台统一 API Key |
| `BAILIAN_OCR_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容 API 根地址 |
| `BAILIAN_OCR_MODEL` | `qwen3.5-ocr` | OCR 模型名 |
| `BAILIAN_OCR_TIMEOUT_SECONDS` | `60` | 单次 HTTP 调用超时 |
| `BAILIAN_OCR_MAX_IMAGE_BYTES` | `10485760` | 图片转 Base64 前的最大字节数 |
| `BAILIAN_OCR_MAX_ATTEMPTS` | `3` | 单张图片或关键帧 OCR 总尝试次数，第一次失败不会立刻降级 |
| `BAILIAN_OCR_RETRY_DELAY_SECONDS` | `2` | 每次 OCR 失败后的重试等待秒数，最后一次失败后不再等待 |

请求 schema：

```json
{
  "model": "qwen3.5-ocr",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,<base64>"
          }
        },
        {
          "type": "text",
          "text": "请只返回图片中的 OCR 文本..."
        }
      ]
    }
  ],
  "temperature": 0
}
```

响应处理：

- 读取 `choices[0].message.content` 作为 OCR 文本；如果内容为 OpenAI 多模态数组，则拼接其中的文本片段。
- `parseEngine` 统一写入 `bailian-qwen-ocr`，`metadata.ocrModel` 写入具体模型名。
- 不记录、不返回、不持久化 API Key。
- HTTP 超时、401/403、5xx 或空响应由 Python 在 OCR 客户端内重试，默认最多 `3` 次；每次失败都会记录 attempt、maxAttempts、filename、imageBytes、errorType、errorMessage 和 nextAttempt，视频关键帧还会通过 `rag_progress` 告诉前端“第 x/n 次错误，准备重试第 x+1 次”。超过最大次数后 Python 再记录 `*_model_degraded`，把所有失败明细写入 warning，并按本地 OCR 降级。

### 状态和错误映射

| 情况 | Python 解析状态 | Java 行为 |
| --- | --- | --- |
| 百炼成功且获得文本 | `READY` 或由整体质量决定 | 保存 parser、摘要、chunk 数 |
| 百炼失败但本地 OCR 或原生解析可用 | 图片主解析通常为 `PARTIAL`；视频关键帧 OCR 属于补充 evidence，若字幕或其他关键帧已可检索，可按整体质量返回 `READY/PARTIAL` | 保留已入库 evidence，控制面板记录 `WARN` 级降级事件，前端继续展示可检索状态 |
| 百炼和本地 OCR 均无可索引文本 | `FAILED` | 返回资料记录，状态为解析失败 |
| Key 未配置 | 不视为接口错误 | 自动跳过百炼并使用本地降级链路 |
| 视频处理任一阶段出现 warning | `PARTIAL` 或由整体质量决定 | Java 从 `parseQuality.messages` 读取阶段位置，写入 `log_error.contextJson.errorLocation` |
