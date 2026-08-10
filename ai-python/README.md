# 学迹智配 Agent：纯 Python FastAPI 后端

## 环境变量配置

真实联调前必须自行补填且不能暴露的变量：

| 环境变量 | 必填场景 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 真实 RAG 联调必填 | 百炼 embedding、rerank、LLM、OCR、ASR 共用。推荐配置为系统环境变量，不要写入 Git。 |
| `MINERU_TOKEN` / `MINERU_API_TOKEN` / `MINERU_API_KEY` | 使用 MinerU 云端能力时必填 | 仅在 MinerU 命令或封装需要云端鉴权时配置。 |
| `SOCIALDATAX_API_KEY` | 抖音 URL 语音转写 RAG 必填 | 只发送到固定 SocialDataX 抖音 MCP endpoint，不写入仓库。 |

可配置的联调项和选填项已集中放在 `ai-python/config/application.yml`：

```yaml
server:
  port: ${AI_SERVICE_PORT:8090}
rag:
  database:
    url: ${RAG_DATABASE_URL:postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic}
dashscope:
  api-key: ${DASHSCOPE_API_KEY:}
```

常用默认值：

- `AI_SERVICE_PORT`：默认 `8090`，React 开发服务器默认代理到 `http://127.0.0.1:8090`
- `RAG_STORE_BACKEND`：默认 `pgvector`
- `RAG_DATABASE_SCHEMA`：默认 `learning_evidence`
- `RAG_VECTOR_DIMENSIONS`：默认 `1024`
- `RAG_EMBEDDING_MODEL`：默认 `text-embedding-v4`
- `RAG_RERANK_MODEL`：默认 `qwen3-rerank`
- `RAG_LLM_MODEL`：默认 `qwen-plus`
- `RAG_TEXT_CORRECTION_ENABLED`：默认 `auto`，存在 `DASHSCOPE_API_KEY` 时在摘要、切块和索引前启用 ASR/OCR 错别字纠正；失败时保留原识别文本
- `RAG_TEXT_CORRECTION_MODEL`：默认 `qwen-plus`，按批纠正口音同音字、OCR 形近字和明显断句错误
- `RAG_TEXT_CORRECTION_BATCH_MAX_ITEMS` / `RAG_TEXT_CORRECTION_BATCH_MAX_CHARS`：默认 `32` / `12000`，限制单次纠错 Prompt 的识别块数和字符数
- `RAG_TEXT_CORRECTION_MIN_SIMILARITY`：默认 `0.55`，差异过大的模型结果会被拒绝，避免纠错节点改写 evidence
- `LLM_IO_MAX_WORKERS`：所有在线 LLM、embedding、rerank、OCR 和 ASR 请求共用的进程级 I/O 线程池，默认 `16`、硬上限 `64`
- `RAG_KAFKA_HANDLER_CONCURRENCY`：Kafka 视频/文档索引长任务并发，CPU/内存阶段默认 `9`（n+1）；同一 partition 内按资料 key 并发
- `RAG_KAFKA_CONTROL_CONCURRENCY`：progress/result/promote/DLQ 控制消息 I/O 并发，默认 `16`（2n）
- `RAG_OUTBOX_PUBLISH_CONCURRENCY`：Outbox Kafka/数据库 I/O 并发，默认 `16`（2n）；不同 topic/key 并行，同一 topic/key 按事件 ID 保序
- `RAG_TASK_WORKER_CONCURRENCY`：查询与 local 索引耐久任务并发，CPU/内存阶段默认 `9`（n+1）
- `RAG_REVIEW_SYNC_WORKERS`：入库后自动复习生成线程数，模型等待属于 I/O，默认 `16`（2n）；不会阻塞 RAG promote 终态回写
- `REVIEW_TASK_WORKER_CONCURRENCY`：复习生成耐久恢复并发，默认 `16`（2n）；从 PostgreSQL 原子领取排队任务和超过租约的 `GENERATING` 任务
- `REVIEW_TASK_WORKER_STALE_SECONDS`：复习任务中断恢复阈值，默认 `1200` 秒；生成阶段持续更新进度时不会被重复领取
- `AGENT_WORKER_CONCURRENCY`：不同 Agent 持久任务并发，模型与数据库等待属于 I/O，默认 `16`（2n）；同一任务仍由任务锁串行
- `REVIEW_LLM_API_KEY`：复习摘要、知识单元发现和卡片生成使用的本机中转密钥；缺失时返回可诊断失败，不生成本地伪内容
- `DEEPSEEK_API_KEY`：可选的复习降级密钥；Cockpit 按长等待方案完成 Terra 重试后仍失败时才直连 DeepSeek
- `REVIEW_COCKPIT_REQUEST_RETRIES`：项目在 DeepSeek 降级前重新请求 Cockpit 的次数，默认 `1`
- `REVIEW_EXTRACTION_TIMEOUT_SECONDS`：单次 Cockpit 客户端等待窗口，默认 `615` 秒
- `REVIEW_GENERATION_MAX_ATTEMPTS`：Actor 卡片质量尝试上限，默认 `8`，安全范围 `1-20`
- `REVIEW_GENERATION_MAX_MERGE_ROUNDS`：多卡 Observer → Merge Repair 合并轮次上限，默认 `4`，安全范围 `1-12`；与 Actor 尝试独立计数
- `RAG_DOUYIN_MCP_ENABLED`：抖音 MCP 语音转写路线开关，默认 `true`
- `RAG_DOUYIN_TRANSCRIPT_POLL_INTERVAL_SECONDS`：抖音转写任务轮询间隔，默认 `5`
- `RAG_DOUYIN_TRANSCRIPT_MAX_WAIT_SECONDS`：抖音单次转写最大等待时间，默认 `900`
- `REVIEW_LANGEXTRACT_MAX_WORKERS`：单份资料同一 pass 的 LangExtract I/O 并发，默认 `16`（2n）、硬上限 `64`
- `REDIS_URL`：可选；用于跨实例复习生成短锁和 Agent L2 运行态快照，不能替代 PostgreSQL 的排程、消息和摘要事实
- `REVIEW_GENERATION_LOCK_TTL_SECONDS`：复习生成短锁 TTL，默认 `180` 秒

Agent、RAG、复习、简历、视觉 OCR、音频 ASR 和识别文本纠错 Prompt 统一维护在 `ai-python/prompts/`；修改模板时应同步更新版本常量和对应测试。

复习生成图在 Actor 通过单卡门禁后会执行多卡片粒度复查。`multi_card_observer` 只返回结构化合并计划，`merge_repair` 只修改计划点名的卡片组，并在合并后重新进入单卡 Observer；合并结果必须保留 knowledge unit、原始问题覆盖、evidence 和答案论断并集。连续候选指纹无变化或合并轮次耗尽时安全进入 `NEEDS_REVIEW`，最后一次完整有效候选和旧活动卡片不被覆盖。

抖音 URL 接入流程与 SocialDataX MCP 工具契约见 `docs/api/remote-video-import.md`。

本机覆盖时复制 `ai-python/config/application.local.example.yml` 为 `ai-python/config/application.local.yml` 后修改。`application.local.yml` 已加入 `.gitignore`，可用于填写本机路径或临时离线模式。

## 启动

### PyCharm 单文件启动

推荐直接运行：

```text
ai-python/run.py
```

PyCharm 配置（`<PROJECT_ROOT>` 指仓库根目录，目录名不参与程序运行）：

- Script path：`<PROJECT_ROOT>\ai-python\run.py`
- Working directory：`<PROJECT_ROOT>\ai-python`
- Python interpreter：`C:\Users\WhenJayHe\miniforge3\envs\learning-evidence-rag\python.exe`

如需直接运行 `ai-python/app/main.py`，当前也已支持，效果等同于调用 `run.py`。启动后访问 `http://127.0.0.1:8090/health` 检查服务状态。

默认会加载：

- `ai-python/config/application.yml`
- `ai-python/config/application.local.yml`，如果文件存在

配置优先级从高到低：

1. PyCharm Environment variables / 系统环境变量
2. 启动参数 `--config` 指定的配置文件
3. `application.local.yml`
4. `application.yml`

因此已经配置系统级 `DASHSCOPE_API_KEY` 时，不需要在 PyCharm 中重复配置。Windows 新增或修改系统环境变量后，需要重启 PyCharm 才能继承最新值。

首次连接空 PostgreSQL 时，可在 PyCharm Parameters 或命令行增加 `--bootstrap-database`；该参数只执行非破坏性建表计划并跳过 `DROP`，已有数据库的日常启动不需要添加。

如需创建本机覆盖配置，复制 `ai-python/config/application.local.example.yml` 为 `ai-python/config/application.local.yml` 后修改。`application.local.yml` 已被 `.gitignore` 忽略，不要提交真实密钥。

PyCharm 的 Parameters 可以留空；如需额外指定配置文件，可填写：

```text
--config config/application.local.yml
```

### 命令行启动

Windows PowerShell 推荐使用项目启动脚本。脚本会统一控制台、Conda 和 Python 的 UTF-8 编码，并关闭 `conda run` 的输出捕获层，避免中文日志被按 GBK 错误解码：

```powershell
.\ai-python\start.ps1
```

需要向 `run.py` 传递排障参数时直接追加，例如：

```powershell
.\ai-python\start.ps1 --without-kafka --without-agent-worker
```

也可以激活环境后直接启动：

```powershell
conda env create -f ai-python/environment.yml
conda activate learning-evidence-rag
python ai-python/run.py
```

上面的命令需要在仓库根目录执行。如果当前已经进入 `ai-python/` 目录，则使用：

```powershell
conda env update -f environment.yml --prune
python run.py
```

已创建过环境时，在仓库根目录使用 `conda env update -f ai-python/environment.yml --prune` 同步依赖；在 `ai-python/` 目录内使用 `conda env update -f environment.yml --prune`。`requirements.txt` 只作为 pip 兼容依赖清单保留。

`environment.yml` 会安装视频抽音频、抽帧和内嵌字幕提取需要的 `ffmpeg/ffprobe`，以及本地 OCR 降级需要的 `tesseract`。`OCR_LANG=chi_sim+eng` 还需要 Tesseract 语言数据中存在 `chi_sim` 和 `eng`；中文语言包未安装时，可临时改为 `eng` 验证 OCR 调用链路。

未配置 `RAG_DATABASE_URL` 时会退回内存后端，主要用于本地单元测试。正式运行使用 PostgreSQL/pgvector；空库先执行 `python -m app.core.database_bootstrap`，详细说明见 `docs/database/postgresql-pgvector.md`。

### Python cron 与耐久 worker

`run.py` 在 API 进程外监督耐久 worker。默认配置会启动 Agent、RAG 任务 worker 和复习生成恢复 worker；启用 Kafka 后会同时启动 Outbox cron 与 Kafka 状态消费 worker。复习恢复 worker 每 2 秒从 PostgreSQL 原子领取 `review.queued` 或超过 20 分钟未更新的 `GENERATING` 资料，默认最多并发处理 16 份。Web 进程重启后，已持久化的复习任务会继续生成；旧活动卡片只在新候选成功发布后替换。

`RAG_VIDEO_PARALLEL_WORKERS=9` 只表示单条视频内部的媒体分段解析线程数，视频解码和解析属于 CPU/内存阶段。
完整 Kafka 本地配置下，`start.ps1` 会启动 FastAPI、cron、Kafka、Agent、RAG durable worker 和复习恢复 worker；Kafka 索引默认可并发处理 9 份不同资料。

```powershell
# 默认启动 FastAPI、Agent worker、RAG 任务 worker和复习恢复 worker。
.\ai-python\start.ps1

# 使用 Kafka 索引链路时再显式启用 broker worker。
$env:RAG_KAFKA_ENABLED='true'
$env:AI_KAFKA_WORKER_ENABLED='true'
.\ai-python\start.ps1
```

可使用 `--without-cron`、`--without-kafka`、`--without-agent-worker` 或 `--without-rag-worker` 做本地排障；`--with-*` 参数可以临时覆盖 YAML 开关。`--without-kafka` 会同时关闭 Kafka 投递模式，资料任务自动改由 PostgreSQL `LOCAL` durable worker 执行；`--with-kafka` 会同时启用 Kafka 投递和 worker。`app.workers.kafka_worker` 是正式 Kafka 入口，`ai-python/run_kafka_worker.py` 仅保留兼容转发。

## 目录结构

- `app/api/`：认证、页面数据、日志、RAG、Agent 和记忆公开接口路由。
- `app/core/`：启动配置读取、YAML 映射和 Uvicorn 启动参数。
- `app/schemas/`：与 React 契约保持一致的 Pydantic 请求/响应模型。
- `app/review/`：资料级复习卡片提炼、FSRS 排程、分组队列、答案揭示和生成并发保护。
- `app/workers/`：Kafka 消费、Outbox 发布、RAG/Agent/复习生成耐久任务和独立 cron 调度。
- `agents/gateway/`：受控本地工具、RAG 和记忆调用网关。
- `agents/llm/`：Agent 规划、执行和回答使用的模型客户端。
- `agents/orchestration/`：统一 PAE/ReAct 状态图及只读、规划辅助函数。
- `prompts/`：Agent、RAG、复习、简历、视觉 OCR 和音频 ASR Prompt 及版本号的集中目录。
- `agents/memory/`：长期记忆候选、冲突判断、索引和检索服务。
- `agents/resume_adapter/`：简历模板填充适配；`agents/note_writer/` 当前仅为预留目录。
- `rag/core/`：RAG 通用模型、元数据过滤和文本清洗。
- `rag/observability/`：RAG 进度上报、过程日志、模型调用日志和 PostgreSQL 持久化。
- `rag/generation/`：百炼 LLM 回答生成和 evidence 引用摘要。
- `rag/loaders/`、`rag/chunkers/`、`rag/indexes/`、`rag/retrievers/`、`rag/rerankers/`：解析、递归切块、索引、检索和重排主链路。
- `rag/evaluation/`：Ragas 小样本评估脚本和兼容层。
- `video/`：视频 ASR、抽帧、OCR、去重和分片证据处理。
- `tests/`：Python 单元测试和接口回归测试。

### Agent 状态恢复

Agent 任务、消息、上下文摘要、审批、会话文件夹和记忆状态均以 PostgreSQL 为权威来源。Redis 只保存带身份校验和 TTL 的可重建上下文快照；miss 或不可用时由 `LocalAgentGateway` 回源 PostgreSQL。Agent worker 从待执行任务中领取工作，任务事件和终态写回数据库；SSE 通过数据库增量事件恢复，断线或进程重启后可继续轮询同一任务。

## 开发验证

Python 测试必须在 `learning-evidence-rag` Conda 环境中执行：

```powershell
conda run --no-capture-output -n learning-evidence-rag python -B -m pytest ai-python/tests -q
```

GitHub Actions 同样根据 `ai-python/environment.yml` 创建该环境，并与 React `npm run build` 分别在独立 job 中验证。

## RAG 评估

小样本 Ragas 评估入口位于 `ai-python/rag/evaluation/run_ragas_small_eval.py`。评估脚本默认使用真实 PostgreSQL/pgvector 和百炼模型链路，并在同一个数据库中写入 `Ragas_Test_` 前缀表；`offline` 仅表示不额外运行 Ragas LLM 指标，不表示使用内存仓库或 hash embedding：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAGAS_TEST_TABLE_PREFIX='Ragas_Test_'
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode offline
```

真实 Ragas 评分需要先按 `docs/testing/ragas-small-evaluation-plan.md` 配置 `RAGAS_EVAL_*` 环境变量。评估 Key 不会写入 `run_config.json` 或日志输出。

## 接口

- `GET /health`
- `/api/auth/*`：登录、当前用户和退出登录。
- `/api/page-data/*`：工作台和系统设置。
- `/api/logs/*`：事件、错误和概览。
- `/api/rag/*`：资料、索引、检索、查询历史与耐久查询任务。
- `/api/agent/*`：任务、会话、审批、SSE、工具和长期记忆。

## RAG 策略

- 多格式解析路由：`pdf/doc/docx/ppt/pptx/md/txt/srt/vtt/xls/xlsx/png/jpg/jpeg/webp`
- MinerU 文档识别适配入口：`MINERU_COMMAND`
- 百炼 OCR 适配入口：`DASHSCOPE_API_KEY`
- 原生结构解析优先：DOCX/PPTX/XLSX/Markdown/TXT 优先保留标题、段落、表格、图片、sheet 和 cell range
- 复杂版式补充解析：低置信或高精度模式时通过 LibreOffice 转 PDF 后补跑 MinerU/OCR
- 递归切块：标题、章节、页面、幻灯片、段落、句子、长度预算；表格、图片和代码块默认原子保存
- 摘要索引：文档摘要与章节摘要
- 混合检索：BM25 + PostgreSQL/pgvector 向量召回
- 融合重排：RRF / RAG-Fusion
- 持久化：`rag_document` 保存资料摘要，`rag_chunk` 保存切块、DocumentBlock/evidence 元数据、词频统计和 `VECTOR(1024)` 向量
- Embedding：默认使用百炼 `text-embedding-v4` 生成 1024 维向量，API Key 读取 `DASHSCOPE_API_KEY`
- 视频证据：第一阶段解析 `.srt/.vtt` 和带时间戳的 `.txt` 转写文本，保留 `startTime/endTime/playbackUrl` 作为证据定位

## 百炼 OCR 接入

图片文件和 PDF 扫描页优先使用百炼 Qwen-OCR；未配置 Key、调用失败或返回空文本时自动降级为本地 `pytesseract`。本地 OCR 需要 Conda 环境中的 `tesseract` 可执行程序和 `OCR_LANG` 对应语言数据。不要把 Key 写入配置文件或提交到 Git。

```powershell
$env:DASHSCOPE_API_KEY='<your-dashscope-api-key>'
$env:BAILIAN_OCR_MODEL='qwen3.5-ocr'
$env:BAILIAN_OCR_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
```

可选项：

- `BAILIAN_OCR_ENABLED`：默认 `auto`，存在 Key 时启用；设置为 `false` 可强制禁用。
- `BAILIAN_OCR_TIMEOUT_SECONDS`：默认 `60`。
- `BAILIAN_OCR_MAX_IMAGE_BYTES`：默认 `7499952`，按 Base64 编码字符串 10MB 上限保守换算；配置更大的值仍会被客户端限制到该上限。
- `BAILIAN_OCR_MAX_ATTEMPTS`：默认 `3`，单张图片或关键帧失败后会先重试，生产可按稳定性调到 `3-5`。
- `BAILIAN_OCR_RETRY_DELAY_SECONDS`：默认 `2`，每次 OCR 失败后等待再重试的秒数。
- `RAG_VIDEO_OCR_BATCH_MAX_SIZE`：默认 `4`，关键帧 OCR 微批最多收集 4 帧；仍是一帧一个兼容接口请求，不依赖未声明的多图 API。
- `RAG_VIDEO_OCR_BATCH_WAIT_MS`：默认 `800`，首帧到达后未满批时的最大等待窗口；满批或输入关闭立即派发。
- `RAG_VIDEO_OCR_MAX_IN_FLIGHT`：默认 `16`，单个媒体分段最多同时执行的远程 OCR 请求，属于 I/O 密集阶段。

`qwen3.5-ocr` 的官方文档未声明单请求多图上限，且 Batch API 支持列表未列出该模型，因此本项目不把多帧拼入一个请求。详细限制和运行契约见 `docs/api/rag.md`。
