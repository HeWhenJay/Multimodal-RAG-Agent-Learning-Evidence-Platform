# RAG 架构说明

## 当前边界

当前运行形态为 `React -> FastAPI -> PostgreSQL/pgvector`。普通上传、分片上传、重建索引、视频处理、查询、Agent、记忆、日志和 SSE 均由 Python 公开控制面或 Python durable worker 完成，不依赖 Spring Boot、Java Tool Gateway、7080 callback 或 Java 内部 Token。具体公开契约见 `docs/api/rag.md` 与 `docs/api/agent.md`。

Agent 由 Python `ai-python/agents/orchestration/pae_react_graph.py` 统一编排 PAE/ReAct 流程，并通过进程内 `LocalAgentGateway` 访问受控资料、RAG、记忆和任务状态。PostgreSQL 是权威持久化来源，Redis 只做可丢失运行态加速；任务恢复、审批和 SSE 重连均从 Python 持久记录重建。

```mermaid
flowchart LR
    A["React 前端"] --> B["Python FastAPI 公开 API"]
    B --> C["Auth / PageData / Logs / RAG / Agent"]
    C --> DB[("PostgreSQL / pgvector")]
    C --> W["Python durable workers + cron"]
    W --> KAFKA{"可选 Kafka"}
    KAFKA -->|"开启"| K["Outbox / Index / Progress / Result / DLQ"]
    KAFKA -->|"关闭"| L["本地 PostgreSQL 任务队列"]
    K --> P["多格式解析、递归切块、摘要索引"]
    L --> P
    P --> V["BM25 + 向量召回 + RRF/RAG-Fusion"]
    V --> R["evidence 引用回答"]
```

Kafka 是可选的高吞吐索引通道，不是公开查询依赖。`/api/rag/query` 和查询任务由 Python 控制面创建并由 Python worker 执行；关闭 Kafka 时使用 PostgreSQL 本地 durable worker，资料状态、进度和最终结果仍保持一致。

## 迁移前历史设计（仅供追溯）

## Kafka 索引流水线

索引链路升级后的关键步骤：

1. Java 上传或重建资料时，在同一事务内写 `learning_material`、`rag_index_job` 和 `rag_outbox_event`。
2. 由唯一的 Outbox Publisher 多实例安全抢占到期事件并发布 `rag.material.index.request.v1`。迁移期 Java 与 Python `app.workers.outbox_publisher` 通过所有权开关互斥；Python cron 使用 `FOR UPDATE SKIP LOCKED`、租约和指数退避复刻原有语义。
3. Python Index Worker 按 `canonicalDocumentId` 串行处理同一资料的索引请求，不同资料可并发；解析、切块、摘要和 embedding 后只写 `stagingDocumentId`。
4. Python 通过 `rag.material.index.progress.v1` 上报节流进度，通过 `rag.material.index.result.v1` 上报 staging 索引终态。
5. Java Result Consumer 写进度、校验 `active_index_job_id` 和 `index_request_version`。只有当前 active job 才发布 `rag.material.index.promote.request.v1`。
6. Python Promote Worker 在执行 pgvector promote 前再次调用 Java active-check；仍为 active job 才能把 staging 复制为 canonical，重复 promote 必须幂等成功。pgvector 层还会拒绝低 `requestVersion` 覆盖高版本 canonical。
7. Java Promote Result Consumer 更新 `learning_material` 和 `rag_index_job` 终态。stale job、过期 retry 或取消 job 不会污染 private 查询。

长视频分片收尾仍由接收最后一个分片的 Java 实例本机 `@Async` 合并并上传对象存储；Kafka 只接管合并完成后的 RAG 索引。`rag.upload.finalize.request.v1` 保留给未来共享 chunkRoot 或 host affinity 模式。

Outbox 表和消费者幂等表承担 Java 侧“至少一次发布、消费幂等”的职责。Python 消费 request 时使用 manual commit：result、retry 或 DLQ 消息发送成功后才提交原 offset，避免 worker 重启造成请求丢失。

## Python RAG 流程

索引阶段：

1. 接收 Java 传入的文件或文本。
2. Python 按文件类型选择解析器，DOCX/PPTX/XLSX/Markdown/TXT 优先走原生结构解析，PDF 优先 MinerU。
3. 解析器统一输出 `DocumentBlock`，保留 block 类型、页码、幻灯片、sheet、cell range、来源路径和解析器置信度。
4. 低置信、截图型或高精度模式时，Python 通过 LibreOffice 转 PDF 后补跑 MinerU/OCR；补跑失败但原生块可用时返回 `PARTIAL`。
5. 按标题、章节、页面、幻灯片、段落、句子和长度预算做递归切块；表格、图片、代码块、公式和图表默认作为原子块。
6. 视频 `frame_ocr` 在递归切块前先做近重复聚合，保留 `duplicateGroupId`、`representativeTime`、`timeRanges` 和 `mergedFrameCount`，避免同一画面以多个原子块重复入库。
7. 为文档和章节建立摘要索引。
8. 为 chunk 建 BM25 词项统计，调用百炼 `text-embedding-v4` 生成 1024 维向量，并写入 PostgreSQL/pgvector 的 `rag_chunk.embedding`，evidence 元数据保存在 `rag_chunk.metadata`。
9. Kafka worker 模式先写 staging 文档，metadata 保留 `canonicalDocumentId/jobId/requestVersion/stagingDocumentId/sourceJobId`；Java 校验通过后再由 Python promote 为 canonical 文档。

查询阶段：

1. 基于原问题生成 Multi-Query 变体。
2. 按 metadata 过滤用户、文档类型、来源和可见范围。
3. 对每个 query 同时执行 BM25 和 pgvector 向量召回。
4. 使用可配置的确定性 weighted RRF 合并多路排名；非法配置自动回退等权 RRF，并在 diagnostics 中保留逐路 rank、raw score 和贡献值。
5. 对融合候选调用百炼 rerank；未配置、显式本地模式或调用失败时使用可解释的确定性特征重排，再按 `duplicateGroupId` 和视频时间窗执行 evidence 多样性过滤。
6. 通过严格 evidence guard 后返回可追溯 evidence，并调用已配置的回答模型生成带引用回答；证据不足时跳过回答模型并结构化拒答。

后续模型训练、可学习融合、领域 reranker、双塔 embedding、视觉检索和模型发布治理不属于当前已实现能力，统一按 [模型微调与可学习融合实施清单](../training/model-finetuning-checklist.md) 推进。

## Stitch 前端视觉基准

前端基于 Chrome 中 Stitch 项目 `学迹智配管理后台` 的生成页面复刻：

| 维度 | 取值 |
| --- | --- |
| 主色 | `#4F46E5` |
| 辅色 | `#0EA5E9` |
| 强调色 | `#A54100` |
| 背景 | `#F9FAFB` |
| 字体 | `Inter`，代码/标签使用 `JetBrains Mono` |
| 卡片圆角 | 约 `8px` |
| 布局 | 左侧固定导航 + 顶部搜索/上传栏 + 信息密度适中的工作台 |

Stitch 页面包含的核心模块：

- 工作台统计卡片。
- 知识库智能检索 RAG。
- 多模态数据接入通道。
- 岗位适配分析入口。
- 视频知识切片回顾。
- 简历证据对齐。
