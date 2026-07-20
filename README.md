# 学迹智配 Agent

学迹智配 Agent 是面向学习证据沉淀和岗位适配的多模态 RAG 与 Agent 项目。当前运行形态为 `React + FastAPI + PostgreSQL/pgvector`：前端所有 `/api/*` 请求默认代理到 FastAPI `8090`，不需要 Spring Boot、JDK、Maven 或 `7080` 服务。

## 架构

```text
React + Vite (5178)
        |
        | /api/* + Bearer Token
        v
FastAPI (8090)
  |- Auth / PageData / Logs
  |- RAG 控制面与对象存储
  |- Agent / Memory / SSE
  |- PostgreSQL durable task worker
  |- Kafka / Outbox worker (可选)
        |
        v
PostgreSQL + pgvector (5433)
```

RAG 使用 MinerU 优先、递归切块、摘要索引、Metadata 过滤、Multi-Query、BM25 与向量混合召回、RRF/RAG-Fusion、重排和 evidence 引用。Agent 使用 LangGraph 编排，任务、消息、审批、记忆和 SSE 事件均以 PostgreSQL 为权威状态。

## 目录

| 路径 | 用途 |
| --- | --- |
| `frontend-react/` | React + Vite 管理后台，开发端口 `5178` |
| `ai-python/` | FastAPI、RAG、Agent、耐久 worker 和启动入口 |
| `infra/sql/` | PostgreSQL/pgvector 初始化与增量迁移 |
| `docs/api/` | 对外 API 契约 |
| `docs/architecture/` | 系统架构与迁移记录 |

## 首次初始化

Python 服务使用 Conda 环境 `learning-evidence-rag`：

```powershell
conda env create -f ai-python/environment.yml
conda activate learning-evidence-rag
```

首次创建本地数据库时，使用 Python 的非破坏性 bootstrap。它读取同一份
`infra/sql/init.sql`，跳过 `DROP` 并将建表、建索引转换为幂等操作：

```powershell
$env:PYTHONPATH = 'ai-python'
conda run -n learning-evidence-rag python -B -m app.core.database_bootstrap --dry-run
conda run -n learning-evidence-rag python -B -m app.core.database_bootstrap
```

已有数据库不要直接执行 `init.sql`。`run.py` 会自动执行仓库内安全、幂等的 Python 增量迁移，例如耐久 RAG 任务表和租约字段。
新环境也可以在首次启动时显式合并为一条命令：

```powershell
conda run -n learning-evidence-rag python -B ai-python/run.py --bootstrap-database
```

本地覆盖配置从 `ai-python/config/application.local.example.yml` 复制为 `ai-python/config/application.local.yml`。密钥和本地配置均不提交。常用环境变量：

| 变量 | 用途 |
| --- | --- |
| `RAG_DATABASE_URL` | PostgreSQL 连接串，默认指向本机 `5433` 的 `learning_evidence` schema |
| `DASHSCOPE_API_KEY` | 百炼 embedding、rerank、LLM、OCR、ASR |
| `MINERU_COMMAND` | 可选 MinerU 命令模板，包含 `{input}` 和 `{output}` |
| `RAG_KAFKA_ENABLED` | 启用 Kafka 索引流水线 |
| `EVIDENCE_STORAGE_PROVIDER` | `local` 或 `oss` 对象存储 |
| `TAVILY_API_KEY` | 仅 Agent 联网检索时需要 |

## 启动

后端一键启动：

```powershell
conda run -n learning-evidence-rag python -B ai-python/run.py
```

默认启动 FastAPI、Agent worker 和 RAG durable worker。启用 `RAG_KAFKA_ENABLED=true` 后，`run.py` 会同时监督 Kafka worker 和 Outbox cron。启动后访问 [http://127.0.0.1:8090/health](http://127.0.0.1:8090/health)。

启动前端：

```powershell
cd frontend-react
npm ci
npm run dev
```

浏览器访问 [http://127.0.0.1:5178](http://127.0.0.1:5178)。`VITE_API_PROXY_TARGET` 未设置时默认使用 `http://127.0.0.1:8090`。

本地排障可使用：

```powershell
python ai-python/run.py --without-cron --without-kafka
python ai-python/run.py --without-agent-worker --without-rag-worker
```

## API

所有公开接口保持 `{code,msg,data}` 结果信封和既有 React 路径：

| 模块 | 路径 |
| --- | --- |
| 认证 | `/api/auth/*` |
| 工作台和设置 | `/api/page-data/*` |
| 系统日志 | `/api/logs/*` |
| 学习资料和 RAG | `/api/rag/*` |
| Agent、审批、记忆和 SSE | `/api/agent/*` |

完整请求、错误和异步状态说明见 [API 文档](docs/api/)。

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
- [RAG 接口契约](docs/api/rag.md)
- [Agent 接口契约](docs/api/agent.md)
- [日志接口契约](docs/api/logs.md)
- [PostgreSQL/pgvector 建库说明](docs/database/postgresql-pgvector.md)
