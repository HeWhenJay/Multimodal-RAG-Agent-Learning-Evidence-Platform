# Python RAG 服务

## 环境变量配置

真实联调前必须自行补填且不能暴露的变量：

| 环境变量 | 必填场景 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 真实 RAG 联调必填 | 百炼 embedding、rerank、LLM、OCR、ASR 共用。推荐配置为系统环境变量，不要写入 Git。 |
| `MINERU_TOKEN` / `MINERU_API_TOKEN` / `MINERU_API_KEY` | 使用 MinerU 云端能力时必填 | 仅在 MinerU 命令或封装需要云端鉴权时配置。 |

可暴露的必填联调项和选填项已集中放在 `ai-python/config/application.yml`，格式与 Java `application.yml` 保持一致：

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

- `AI_SERVICE_PORT`：默认 `8090`，Java 后端默认访问 `http://127.0.0.1:8090`
- `RAG_STORE_BACKEND`：默认 `pgvector`
- `RAG_DATABASE_SCHEMA`：默认 `learning_evidence`
- `RAG_VECTOR_DIMENSIONS`：默认 `1024`
- `RAG_EMBEDDING_MODEL`：默认 `text-embedding-v4`
- `RAG_RERANK_MODEL`：默认 `qwen3-rerank`
- `RAG_LLM_MODEL`：默认 `qwen-plus`

本机覆盖时复制 `ai-python/config/application.local.example.yml` 为 `ai-python/config/application.local.yml` 后修改。`application.local.yml` 已加入 `.gitignore`，可用于填写本机路径或临时离线模式。

## 启动

### PyCharm 单文件启动

推荐直接运行：

```text
ai-python/run.py
```

PyCharm 配置：

- Script path：`C:\Users\WhenJayHe\IdeaProjects\Multimodal-RAG-Agent-Learning-Evidence-Platform-React-Java-Python\ai-python\run.py`
- Working directory：`C:\Users\WhenJayHe\IdeaProjects\Multimodal-RAG-Agent-Learning-Evidence-Platform-React-Java-Python\ai-python`
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

如需创建本机覆盖配置，复制 `ai-python/config/application.local.example.yml` 为 `ai-python/config/application.local.yml` 后修改。`application.local.yml` 已被 `.gitignore` 忽略，不要提交真实密钥。

PyCharm 的 Parameters 可以留空；如需额外指定配置文件，可填写：

```text
--config config/application.local.yml
```

### 命令行启动

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

未配置 `RAG_DATABASE_URL` 时会退回内存后端，主要用于本地单元测试。正式运行使用 PostgreSQL/pgvector，建库和建表语句见 `docs/database/postgresql-pgvector.md` 与 `infra/sql/init.sql`。

## 目录结构

- `app/api/`：FastAPI 内部接口路由。
- `app/core/`：启动配置读取、YAML 映射和 Uvicorn 启动参数。
- `app/schemas/`：Java 与 Python 之间共享的 Pydantic 请求/响应模型。
- `agents/`：按职责拆分的 Agent 编排实现，包括 `gateway/`、`read_only/`、`jd_learning_plan/`、`memory/` 和 `resume_adapter/`。
- `agent/`：旧导入路径兼容层，新代码优先使用 `agents.*`。
- `rag/`：文档解析、递归切块、索引、检索、重排、回答守卫和评估能力。
- `video/`：视频 ASR、抽帧、OCR、去重和分片证据处理。
- `tests/`：Python 单元测试和接口回归测试。

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
- `POST /internal/rag/documents/index-text`
- `POST /internal/rag/documents/index-file`
- `GET /internal/rag/documents/{document_id}/evidences`
- `POST /internal/rag/query`
- `POST /internal/rag/jd-analysis`
- `GET /internal/rag/overview`

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
- `BAILIAN_OCR_MAX_IMAGE_BYTES`：默认 `10485760`。
- `BAILIAN_OCR_MAX_ATTEMPTS`：默认 `3`，单张图片或关键帧失败后会先重试，生产可按稳定性调到 `3-5`。
- `BAILIAN_OCR_RETRY_DELAY_SECONDS`：默认 `2`，每次 OCR 失败后等待再重试的秒数。
