# 学迹智配 Agent

学迹智配 Agent 是面向学习证据沉淀、资料检索和岗位准备的多模态 RAG 与 Agent 项目。当前完整运行形态为 **`React + FastAPI + PostgreSQL/pgvector`**：React 只调用 FastAPI `8090`，Python 直接负责认证、页面数据、日志、RAG、Agent、记忆、SSE 和耐久任务。Spring Boot、JDK、Maven 和 `7080` 都不是当前运行依赖。

![React](https://img.shields.io/badge/React-18-149ECA?logo=react&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Python-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/Agent-LangGraph-1C3C3C)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)
![RAG](https://img.shields.io/badge/RAG-Hybrid%20Search-EA4335)
![Kafka](https://img.shields.io/badge/Kafka-Optional-231F20?logo=apachekafka&logoColor=white)

## 运行结论

完整程序只需要启动以下两个应用进程：

```powershell
conda run -n learning-evidence-rag python -B ai-python/run.py

cd frontend-react
npm run dev
```

- 前端地址：<http://127.0.0.1:5178>
- Python API：<http://127.0.0.1:8090>
- 健康检查：<http://127.0.0.1:8090/health>
- 数据库：PostgreSQL + pgvector，默认 `127.0.0.1:5433`
- 原始文件：本地目录或阿里 OSS；Kafka 仅在需要高吞吐索引时启用。

`ai-python/run.py` 是后端唯一启动入口。默认会监督 FastAPI、Agent worker、RAG durable worker 和已启用的 cron；Kafka 配置为开启时才会额外启动 Kafka worker。Java 代码只应视为迁移历史，不应启动或配置为联调依赖。

## 项目能力

- 多模态资料入库：文本、PDF、Office 文档、图片、字幕与视频；PDF 优先 MinerU，失败时走本地降级解析。
- 可追溯 RAG：结构化解析、递归切块、文档/章节摘要、元数据隔离、BM25 与 pgvector 向量召回、Multi-Query、RRF/RAG-Fusion、重排和 evidence 引用。
- 耐久任务：资料索引、查询任务、Agent 任务都先写入 PostgreSQL，再由 worker 以租约领取；进程重启后可恢复，不依赖 Web 请求进程存活。
- Agent 工作台：LangGraph PAE/ReAct 编排、受控工具、记忆、审批、撤销、任务消息、事件投影与 SSE。
- 统一业务边界：所有公开接口保持 React 既有 `/api/*` 路径、Bearer Token、camelCase 字段和 `{code,msg,data}` 响应信封。

## 系统总览

```mermaid
flowchart TB
    U["用户浏览器"] --> FE["React + Vite\n127.0.0.1:5178"]
    FE -->|"/api/* + Bearer Token\n默认代理到 8090"| API

    subgraph PY["纯 Python 后端：ai-python/"]
        SUP["run.py 统一监督"]
        API["FastAPI 公开控制面\nAuth / PageData / Logs\nRAG / Agent / Memory / SSE"]
        AGW["Agent durable worker\nLangGraph PAE/ReAct"]
        RAGW["RAG durable worker\n查询任务 + LOCAL 索引"]
        CRON["cron\nOutbox / staging 清理"]
        KAFKAW["Kafka worker\n仅 Kafka 模式"]
        SUP --> API
        SUP --> AGW
        SUP --> RAGW
        SUP --> CRON
        SUP -. "RAG_KAFKA_ENABLED=true" .-> KAFKAW
    end

    API <--> DB[("PostgreSQL + pgvector\n业务数据、任务、日志、记忆\nRAG canonical / staging 索引")]
    AGW <--> DB
    RAGW <--> DB
    CRON <--> DB
    KAFKAW <--> DB

    API <--> STORE["原始文件存储\nlocal 或 Aliyun OSS"]
    RAGW <--> STORE
    KAFKAW <--> STORE
    RAGW --> MODEL["MinerU / OCR / ASR\nEmbedding / Rerank / LLM"]
    KAFKAW --> MODEL

    CRON -->|"可选 Outbox 发布"| KAFKA[("Kafka")]
    KAFKA <--> KAFKAW
```

**数据事实源：** PostgreSQL/pgvector 同时保存认证、资料、任务、消息、审批、记忆、日志和向量索引。Redis 如启用只用于可丢失的运行态加速，原始文件保存在本地受控目录或 OSS。没有任何业务状态需要回写 Java。

## 资料入库与索引流程

资料上传不会在 HTTP 请求内同步执行解析或 embedding。FastAPI 先完成权限校验、原始文件落盘和事务写入，再由独立 worker 接管长任务；因此刷新页面、重启 API 或网络短暂波动不会让已提交资料丢失。

```mermaid
flowchart TB
    U["上传文本、文件或视频分片"] --> FE["React 上传与进度轮询"]
    FE --> API["FastAPI RAG 控制面\n校验 Token、用户与文件边界"]
    API --> STORE["保存原始文件\nlocal / OSS"]
    API --> TX["同一 PostgreSQL 事务\nlearning_material\nrag_index_job\n任务投递记录"]

    TX --> MODE{"RAG_KAFKA_ENABLED"}
    MODE -->|"false，默认"| LOCAL["LOCAL 索引任务\nPostgreSQL 租约队列"]
    MODE -->|"true，可选"| OUTBOX["rag_outbox_event"]
    OUTBOX --> CRON["Python Outbox cron"]
    CRON --> KAFKA[("Kafka index request")]

    LOCAL --> RAGW["RAG durable worker"]
    KAFKA --> KAFKAW["Python Kafka worker"]
    RAGW --> PARSE
    KAFKAW --> PARSE

    PARSE["多格式解析\nMinerU 优先，OCR / ASR / 本地降级"] --> BLOCK["DocumentBlock\n保留页码、章节、时间戳、来源"]
    BLOCK --> CHUNK["递归切块\n标题 -> 段落 -> 句子 -> 长度预算"]
    CHUNK --> SUMMARY["文档摘要与章节摘要"]
    SUMMARY --> INDEX["BM25 词项 + 1024 维 embedding\n写入 staging pgvector"]
    INDEX --> CHECK["校验 active job 与 requestVersion\n拒绝过期结果覆盖"]
    CHECK --> PROMOTE["staging promote -> canonical private 索引"]
    PROMOTE --> RESULT["写回 READY / PARTIAL / FAILED\n进度、切块数、受控错误摘要"]
    RESULT --> DB[("PostgreSQL + pgvector")]
    RESULT --> FE
```

`--without-kafka` 是完整的本地模式开关：它会同时关闭 `RAG_KAFKA_ENABLED` 和 `AI_KAFKA_WORKER_ENABLED`，新资料一定创建 `LOCAL` 任务并由 RAG durable worker 消费。这样不会出现“API 投递 Kafka 任务，但 Kafka worker 没有启动”的悬挂任务。`--with-kafka` 则同时启用 Kafka 投递和 Kafka worker。

资料状态：`PENDING -> PARSING -> READY / PARTIAL / FAILED`；重建时为 `REINDEXING`。`PARTIAL` 代表部分补充解析失败但已有可检索 evidence，不是接口失败。

## 视频证据处理流程

视频与字幕资料走同一份 Python 索引状态机。字幕、语音、关键帧和 OCR 文本都带有时间位置，最终 evidence 可以让前端定位到对应播放片段。

```mermaid
flowchart TB
    V["视频、字幕或转写文本"] --> STORE["受控原始文件存储\nlocal / OSS"]
    STORE --> WORKER["Python RAG worker"]
    WORKER --> SUB["内嵌/同目录字幕\n或 FFmpeg 分段 ASR"]
    WORKER --> FRAME["关键帧采样\nPPT 翻页检测"]
    FRAME --> OCR["OCR 与近重复去重"]
    SUB --> BLOCK["带 startTime / endTime 的证据块"]
    OCR --> BLOCK
    BLOCK --> SUMMARY["视频片段摘要"]
    SUMMARY --> CHUNK["递归切块与元数据"]
    CHUNK --> INDEX["pgvector 索引\nBM25 + embedding"]
    INDEX --> EVIDENCE["含时间定位的 evidence\n前端可跳转播放"]
```

## RAG 查询与证据回答流程

查询强制按当前登录用户和 `private` 可见范围过滤。无论是同步查询还是带进度的查询任务，最终回答都返回资料标题、章节、片段、来源、位置和分数等 evidence 结构；证据不足时返回结构化拒答，而不是编造答案。

```mermaid
flowchart TB
    Q["用户问题"] --> FE["React 工作台 / 知识库"]
    FE --> API["FastAPI /api/rag/query\n或 /api/rag/query/tasks"]
    API --> AUTH["从 Bearer Token 推导当前用户\n覆盖客户端传入 userId"]
    AUTH --> TASK{"同步查询\n或 durable 查询任务"}
    TASK --> RETRIEVE

    subgraph RETRIEVE["Python RAG 检索流水线"]
        MQ["Multi-Query\n原问题 + 查询变体"]
        FILTER["元数据过滤\nuserId + visibilityScope=private\n类型、来源、章节等"]
        BM25["BM25 词项召回"]
        VECTOR["pgvector 语义召回"]
        FUSION["weighted RRF\nRAG-Fusion"]
        PARENT["父段聚合"]
        RERANK["百炼 rerank\n或可解释本地重排"]
        DIVERSITY["evidence 多样性过滤\n去除近重复与重叠视频片段"]
        GUARD{"严格 evidence guard"}
        MQ --> FILTER
        FILTER --> BM25
        FILTER --> VECTOR
        BM25 --> FUSION
        VECTOR --> FUSION
        FUSION --> PARENT --> RERANK --> DIVERSITY --> GUARD
    end

    GUARD -->|"证据充分"| LLM["LLM 生成带引用回答"]
    GUARD -->|"证据不足"| REFUSE["REFUSED\n说明拒答原因"]
    LLM --> EVIDENCE["answer + evidences\n标题、章节、片段、来源、分数"]
    REFUSE --> EVIDENCE
    EVIDENCE --> HISTORY["写入查询历史 / 任务进度\nPostgreSQL"]
    HISTORY --> FE
```

RAG 检索设计采用 Multi-Query 扩展召回范围，再对每个查询的 BM25 与向量排名执行 RRF 融合。这样既保留关键词精确匹配，也保留语义召回，并能在 evidence guard 前保留可解释的检索诊断。

## Agent、记忆与审批闭环

Agent 不通过内部 HTTP 或 Java gateway 回调自身。FastAPI 将任务和用户操作持久化后，Agent worker 使用进程内 `LocalAgentGateway` 调用受控 RAG、记忆和业务服务；每个事件先落 PostgreSQL，再通过 SSE 投影到前端。

### 耐久任务与事件投影

```mermaid
flowchart TB
    U["用户输入任务"] --> FE["React Agent 工作台"]
    FE --> API["FastAPI /api/agent/*\n认证、所有权与 Result 信封"]
    API --> TASK["PostgreSQL\nagent_task / message / event / review / operation"]
    TASK --> AGW["Agent durable worker\nPostgreSQL advisory lock"]
    AGW --> GRAPH["LangGraph PAE + ReAct\n稳定 threadId 执行或恢复"]
    GRAPH --> GATE["LocalAgentGateway\n白名单、所有权、审批与幂等边界"]

    GATE --> RAG["Python RAG\n当前用户 private evidence"]
    GATE --> MEM["Agent 记忆\n检索与待确认候选"]
    GATE --> OPS["受控变更\n快照与 undo"]
    RAG --> DB[("PostgreSQL + pgvector")]
    MEM --> DB
    OPS --> DB

    GRAPH --> EVENT["持久化任务状态、消息\n节点事件、工具观察与草稿"]
    EVENT --> DB
    EVENT --> SSE["SSE task / agent_event / done"]
    SSE --> FE

    GRAPH --> REVIEW{"需要用户确认"}
    REVIEW -->|"否"| EVENT
    REVIEW -->|"是"| WAIT["WAITING_PLAN_REVIEW\nWAITING_OUTPUT_REVIEW\nWAITING_CRUD_REVIEW"]
    WAIT --> EVENT
    FE -->|"APPROVED / REJECTED\nCHANGES_REQUESTED"| DECIDE["POST review decide"]
    DECIDE --> DB
    DB --> AGW
```

### LangGraph PAE + ReAct 节点编排

这张图严格按 `ai-python/agents/orchestration/pae_react_graph.py` 中 `build_unified_graph()` 的真实节点和条件边重绘。一次 Agent 请求在进入 `StateGraph` 前会先由 `start_unified_agent()` 或 `resume_unified_agent()` 构造 `initial_state`；进入图后的第一个节点是 `conversation_title`，随后是 `context_restore -> task_router`。只有路由完成后，`planning_task` 才进入 `planner`，只读任务会先经过 `memory_prefetch_before_planner` 再进入 `planner`。

```mermaid
flowchart TB
    START["start_unified_agent / resume_unified_agent<br/>构造 initial_state 后 invoke StateGraph"] --> TITLE["conversation_title<br/>生成侧边栏会话标题"]
    TITLE --> CONTEXT["context_restore<br/>恢复消息、摘要与上下文预算"]
    CONTEXT --> ROUTER["task_router<br/>标记 planning 或 read_only 子图"]

    ROUTER -->|"planning_task"| PLANNER["planner<br/>生成 PAE 计划、完成标准与工具范围"]
    ROUTER -->|"pure_read_query / read / general"| PREPLAN["memory_prefetch_before_planner<br/>只读任务规划前读取 ACTIVE memory"]
    PREPLAN --> PLANNER

    PLANNER --> ROUTE_PLANNER{"route_after_planner"}
    ROUTE_PLANNER -->|"plan_review"| PLAN_REVIEW["plan_review<br/>发布 PLAN 审批请求"]
    PLAN_REVIEW --> WAIT_PLAN["WAITING_PLAN_REVIEW<br/>StateGraph 本轮结束"]

    ROUTE_PLANNER -->|"resume_rewrite_decision"| REWRITE_DECISION["resume_rewrite_decision<br/>判断是否进入简历证据改写子图"]
    ROUTE_PLANNER -->|"memory_prefetch_after_planner"| POSTPLAN["memory_prefetch_after_planner<br/>执行前补充任务级记忆"]

    REWRITE_DECISION --> ROUTE_REWRITE{"route_after_resume_rewrite_decision"}
    ROUTE_REWRITE -->|"resume_jd_analyzer"| JD_ANALYZER["resume_jd_analyzer<br/>将岗位 JD 归纳为 requirement ID 画像"]
    ROUTE_REWRITE -->|"memory_prefetch_after_planner"| POSTPLAN

    subgraph RESUME_REWRITE["简历证据改写链：只生成待审候选，不直接写 DOCX 或数据库"]
        direction TB
        JD_ANALYZER --> EVIDENCE_RETRIEVER["resume_evidence_retriever<br/>按 JD 要求检索当前用户私有 evidence"]
        EVIDENCE_RETRIEVER -.-> RAG_PROBE["rag_query_probe_non_persistent<br/>内部只读工具调用；不是 StateGraph 节点"]
        EVIDENCE_RETRIEVER --> EVIDENCE_SUMMARIZER["resume_evidence_summarizer<br/>保留 evidenceId / 标题 / 章节 / 片段 / 来源 / 分数"]
        EVIDENCE_SUMMARIZER --> REVISION_ADVISOR["resume_revision_advisor<br/>基于 JD、原简历与 evidence 生成字段级修改建议"]
        REVISION_ADVISOR --> PATCH_BUILDER["resume_patch_builder<br/>确定性整理候选；不写 DOCX"]
        PATCH_BUILDER --> REWRITE_ACCEPT["resume_rewrite_acceptance<br/>验收字段、引文、风险与缺口"]
        PATCH_BUILDER -.-> PATCHES["payload.patches<br/>字段候选数据，不是执行节点"]
        PATCH_BUILDER -.-> GAPS["payload.gapSuggestions<br/>独立补强建议，不是执行节点"]
    end

    REWRITE_ACCEPT --> ROUTE_REWRITE_ACCEPT{"route_after_resume_rewrite_acceptance"}
    ROUTE_REWRITE_ACCEPT -->|"answer_writer"| ANSWER["answer_writer<br/>发布草稿、失败摘要或最终回答"]

    POSTPLAN --> EXECUTOR["executor<br/>选择当前步骤的 ReAct action"]
    EXECUTOR --> ROUTE_EXECUTOR{"route_after_executor"}
    ROUTE_EXECUTOR -->|"tool_adapter"| TOOL["tool_adapter<br/>LocalAgentGateway 执行白名单工具"]
    ROUTE_EXECUTOR -->|"acceptance"| ACCEPT["acceptance<br/>校验完成标准与工具观察"]

    TOOL --> ROUTE_TOOL{"route_after_tool_adapter"}
    ROUTE_TOOL -->|"acceptance"| ACCEPT
    ROUTE_TOOL -->|"repair"| REPAIR["repair<br/>RETRY / SKIP_TOOL / REPLAN / REPORT_UNABLE"]

    REPAIR --> ROUTE_REPAIR{"route_after_repair"}
    ROUTE_REPAIR -->|"tool_adapter"| TOOL
    ROUTE_REPAIR -->|"planner"| PLANNER
    ROUTE_REPAIR -->|"acceptance"| ACCEPT

    ACCEPT --> ROUTE_ACCEPT{"route_after_acceptance"}
    ROUTE_ACCEPT -->|"executor"| EXECUTOR
    ROUTE_ACCEPT -->|"repair"| REPAIR
    ROUTE_ACCEPT -->|"answer_writer"| ANSWER

    ANSWER --> MEMORY["post_answer_memory<br/>仅 COMPLETED 且显式需要时生成 PENDING_REVIEW 记忆候选"]
    MEMORY --> GRAPH_END["END<br/>StateGraph 本轮结束"]

    ANSWER -.-> WAIT_OUTPUT["WAITING_OUTPUT_REVIEW<br/>answer_writer 发布 OUTPUT 审批事件<br/>等待用户确认输出草稿"]

    WAIT_PLAN -->|"APPROVED / CHANGES_REQUESTED"| RESUME_AGAIN["resume_unified_agent<br/>重新构造 initial_state 并再次 invoke 完整图"]
    WAIT_OUTPUT -->|"CHANGES_REQUESTED"| RESUME_AGAIN
    WAIT_OUTPUT -->|"APPROVED"| OUTPUT_REVIEW["resume_output_review<br/>非 StateGraph 节点"]
    WAIT_PLAN -->|"REJECTED"| REVIEW_FAILED["TASK_FAILED<br/>用户拒绝审批"]
    WAIT_OUTPUT -->|"REJECTED"| REVIEW_FAILED
    RESUME_AGAIN --> TITLE

    OUTPUT_REVIEW --> SAVE_GATE{"should_request_crud_review"}
    SAVE_GATE -->|"否"| OUTPUT_DONE["TASK_COMPLETED<br/>输出已确认"]
    SAVE_GATE -->|"是"| WAIT_CRUD["WAITING_CRUD_REVIEW<br/>等待保存类变更审批"]
    WAIT_CRUD -->|"APPROVED"| MUTATION["execute_approved_mutation<br/>非 StateGraph 节点；执行受控变更"]
    WAIT_CRUD -->|"CHANGES_REQUESTED"| RESUME_AGAIN
    WAIT_CRUD -->|"REJECTED"| REVIEW_FAILED
```

`resume_output_review` 和 `execute_approved_mutation` 是审批恢复函数，不是 `StateGraph` 节点。当前生产运行面不提供在线 DOCX 导出；若未来接入模板导出，仍需在该受控审批链外补充独立 API 契约、原文 hash、evidence、长度与版式校验。

任务、消息、审批、操作快照和记忆都以 PostgreSQL 为权威记录。工具失败只能有限重试、降级、重新规划或受控失败；`AGENT_GRAPH_RECURSION_LIMIT=24` 会终止异常循环。连接中断后的前端可以重新读取任务快照并重新连接 SSE；worker 重启后可继续领取未完成的耐久任务。

## 运行模式与进程职责

| 模式 | 资料索引通道 | `run.py` 启动的关键进程 | 适用场景 |
| --- | --- | --- | --- |
| 默认本地模式 | PostgreSQL `LOCAL` durable job | FastAPI、Agent worker、RAG durable worker、已启用 cron | 本机开发、单机部署、无需 Kafka |
| Kafka 高吞吐模式 | PostgreSQL Outbox -> Kafka -> Kafka worker | 默认进程加 Kafka worker | 多资料并发、独立 Kafka 集群 |
| 排障本地模式 | 强制 `LOCAL` durable job | `python ai-python/run.py --without-kafka` | Kafka 暂不可用或只排查 Python 链路 |

`run.py` 会在退出时回收它启动的子进程。worker 不在 Uvicorn Web 进程内运行，避免 reload 导致重复消费或丢失长任务。

## 目录结构

| 路径 | 用途 |
| --- | --- |
| `frontend-react/` | React + Vite 管理后台，开发端口 `5178` |
| `ai-python/app/` | FastAPI 公开 API、认证、页面数据、日志、持久任务、对象存储和 worker |
| `ai-python/rag/` | 解析、递归切块、摘要、pgvector、混合检索、融合、重排与 evidence |
| `ai-python/agents/` | LangGraph 编排与进程内 Agent gateway |
| `ai-python/run.py` | FastAPI 与所有受管 Python worker 的唯一启动入口 |
| `infra/sql/` | PostgreSQL/pgvector 初始化脚本与增量迁移 |
| `docs/api/` | Auth、PageData、Logs、RAG、Agent 和 Memory API 契约 |
| `docs/architecture/` | 纯 Python 后端迁移与 RAG 架构说明 |

## 首次初始化与数据库

Python 使用 Conda 环境 `learning-evidence-rag`：

```powershell
conda env create -f ai-python/environment.yml
conda activate learning-evidence-rag
```

空数据库使用 Python 非破坏性 bootstrap。它读取同一份 `infra/sql/init.sql`，跳过 `DROP`，并把建表和建索引转换为幂等操作：

```powershell
$env:PYTHONPATH = 'ai-python'
conda run -n learning-evidence-rag python -B -m app.core.database_bootstrap --dry-run
conda run -n learning-evidence-rag python -B -m app.core.database_bootstrap
```

已有数据库不要反复执行 `init.sql`。`run.py` 启动时只执行仓库内的 Python 幂等增量迁移；也可以在新环境首次启动时合并为：

```powershell
conda run -n learning-evidence-rag python -B ai-python/run.py --bootstrap-database
```

`backend-java/src/main/resources/application.yml` 的 Spring 配置没有被 Python 复用。Python 从 `ai-python/config/application.yml` 加载非敏感默认值，并允许 `ai-python/config/application.local.yml` 和环境变量覆盖；业务数据、任务与索引统一使用 PostgreSQL `learning_evidence` schema。详细说明见 [PostgreSQL/pgvector 建库说明](docs/database/postgresql-pgvector.md)。

## 配置与启动

将 `ai-python/config/application.local.example.yml` 复制为 `ai-python/config/application.local.yml`，本地密钥和覆盖配置均不提交。常用配置如下：

| 变量 | 用途 |
| --- | --- |
| `RAG_DATABASE_URL` | PostgreSQL 连接串，默认使用本机 `5433` 的 `learning_evidence` schema |
| `DASHSCOPE_API_KEY` | 百炼 embedding、rerank、LLM、OCR 与 ASR |
| `MINERU_COMMAND` | 可选 MinerU 命令模板，使用 `{input}` 与 `{output}` 占位符 |
| `EVIDENCE_STORAGE_PROVIDER` | `local` 或 `oss` 原始文件存储 |
| `RAG_KAFKA_ENABLED` | 启用 Kafka 索引通道；默认 `false` |
| `TAVILY_API_KEY` | 预留配置；当前纯 Python Agent 尚未启用联网搜索，默认留空 |

启动后端：

```powershell
conda run -n learning-evidence-rag python -B ai-python/run.py
```

本地排障：

```powershell
conda run -n learning-evidence-rag python -B ai-python/run.py --without-kafka
conda run -n learning-evidence-rag python -B ai-python/run.py --without-cron --without-agent-worker --without-rag-worker
```

启动前端：

```powershell
cd frontend-react
npm ci
npm run dev
```

`VITE_API_PROXY_TARGET` 未设置时，前端默认代理到 `http://127.0.0.1:8090`。

## 公开 API

| 模块 | 路径 |
| --- | --- |
| 认证 | `/api/auth/*` |
| 工作台和设置 | `/api/page-data/*` |
| 系统日志 | `/api/logs/*` |
| 学习资料和 RAG | `/api/rag/*` |
| Agent、审批、记忆和 SSE | `/api/agent/*` |

完整请求、鉴权、错误和异步状态说明见 [API 文档](docs/api/)。

## 验证

```powershell
conda run -n learning-evidence-rag python -B -m pytest ai-python/tests -q

cd frontend-react
npm run build
```

RAG 小样本评估入口：

```powershell
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode offline
```

## 设计资料

- [纯 Python FastAPI 后端迁移计划](docs/architecture/python-backend-migration-plan.md)
- [RAG 架构说明](docs/architecture/rag-architecture.md)
- [RAG 接口契约](docs/api/rag.md)
- [Agent 接口契约](docs/api/agent.md)
- [日志接口契约](docs/api/logs.md)
- [PostgreSQL/pgvector 建库说明](docs/database/postgresql-pgvector.md)
