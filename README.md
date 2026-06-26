# 学迹智配 Agent：基于 RAG 的多模态学习证据库与岗位适配系统

## 启动前环境变量

### 必须补填且不能暴露

这些值不要写进 Git，也不要直接提交到 `application.yml`。推荐配置为 Windows 系统环境变量；如果用 PyCharm 启动 Python AI 服务，新增或修改系统环境变量后需要重启 PyCharm。

| 环境变量 | 必填场景 | 用途 | 当前配置写法 |
| --- | --- | --- | --- |
| `DASHSCOPE_API_KEY` | 真实 RAG 联调必填 | Python AI 调用百炼 embedding、rerank、LLM、OCR 和 ASR | `ai-python/config/application.yml` 中为 `${DASHSCOPE_API_KEY:}` |
| `MINERU_TOKEN` / `MINERU_API_TOKEN` / `MINERU_API_KEY` | 使用 MinerU 云端能力时必填 | MinerU 命令或第三方封装读取鉴权 | `ai-python/config/application.yml` 中默认留空 |
| `ALIYUN_OSS_ACCESS_KEY_ID` / `ALIYUN_OSS_ACCESS_KEY_SECRET` | Java 存储切到 `oss` 时必填 | Java 后端上传原始资料到阿里云 OSS | `backend-java/src/main/resources/application.yml` 中默认读取环境变量 |
| `EVIDENCE_INTERNAL_LOG_TOKEN` | 需要保护内部日志上报接口时填写 | Java 内部日志接口鉴权 token | `backend-java/src/main/resources/application.yml` 中默认读取环境变量 |

本地开发如果继续使用 `local` 文件存储，只需要先确认 `DASHSCOPE_API_KEY` 已在系统环境变量中配置。数据库密码当前使用本地默认值 `123456`，只适合本机开发；部署或共享环境应改为环境变量。

### 选填变量与可暴露默认值

Python AI 服务现在使用类似 Java 的配置格式，默认读取 `ai-python/config/application.yml`，可复制 `ai-python/config/application.local.example.yml` 为 `ai-python/config/application.local.yml` 做本机覆盖。`application.local.yml` 已被 `.gitignore` 忽略。

已在 Python 默认配置中填好的可暴露联调值：

| 配置项 | 默认值 | 修改方式 |
| --- | --- | --- |
| `server.port` / `AI_SERVICE_PORT` | `8090` | 修改 `ai-python/config/application.yml`，或设置环境变量 `AI_SERVICE_PORT` |
| `rag.store.backend` / `RAG_STORE_BACKEND` | `pgvector` | 离线演示可改为 `memory` |
| `rag.database.url` / `RAG_DATABASE_URL` | `postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic` | 本机 PostgreSQL 端口、账号或 schema 不同时修改 |
| `rag.database.schema` / `RAG_DATABASE_SCHEMA` | `learning_evidence` | 与数据库 schema 保持一致 |
| `rag.vector.dimensions` / `RAG_VECTOR_DIMENSIONS` | `1024` | 必须与 pgvector 表中 `VECTOR(1024)` 一致 |
| `rag.embedding.model` / `RAG_EMBEDDING_MODEL` | `text-embedding-v4` | 更换百炼 embedding 模型时修改 |
| `rag.rerank.model` / `RAG_RERANK_MODEL` | `qwen3-rerank` | 更换百炼 rerank 模型时修改 |
| `rag.llm.model` / `RAG_LLM_MODEL` | `qwen-plus` | 更换回答生成模型时修改 |

常用选填项：

| 配置项 | 默认值 | 何时修改 |
| --- | --- | --- |
| `mineru.command` / `MINERU_COMMAND` | 空 | 安装 MinerU 并希望优先用 MinerU 解析 PDF 时填写，例如 `mineru -p {input} -o {output}` |
| `video.ffmpeg-command` / `FFMPEG_COMMAND` | 空 | conda 环境默认通过 `ffmpeg` 包提供；环境外运行且不在 PATH 时填写完整路径 |
| `video.ffprobe-command` / `FFPROBE_COMMAND` | 空 | conda 环境默认随 `ffmpeg` 包提供；环境外运行且不在 PATH 时填写完整路径 |
| `document.convert.libreoffice-command` / `LIBREOFFICE_COMMAND` | 空 | LibreOffice 不在 PATH 且需要 DOC/PPT 转 PDF 补充解析时填写 |
| `ocr.lang` / `OCR_LANG` | `chi_sim+eng` | 本地 OCR 语言包不同时修改 |

配置占位符格式与 Java 保持一致，例如：

```yaml
rag:
  database:
    url: ${RAG_DATABASE_URL:postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic}
dashscope:
  api-key: ${DASHSCOPE_API_KEY:}
```

英文标识：Multimodal RAG Agent Learning Evidence Platform

技术栈：React + Java Spring Boot + Python FastAPI + RAG。当前阶段完成到 RAG，暂不实现 Agent 任务编排。

## 项目定位

本项目面向大学生与求职准备人群，用于把个人学习资料、课程笔记、项目材料、视频片段和简历内容沉淀为可检索、可引用、可复用的个人学习证据库。系统第一阶段聚焦 RAG：

- 文档识别：优先使用 MinerU，通过 `MINERU_COMMAND` 接入；未配置时走本地解析降级。
- 切块：使用递归切块，优先保留标题、段落、句子结构。
- 检索：Multi-Query + BM25 + PostgreSQL/pgvector 向量召回 + RRF/RAG-Fusion。
- 引用：回答返回证据片段、来源、章节和分数。

## RAG 业务流程

本项目的 RAG 不是前端直接调用 Python，也不是把 AI 逻辑写在 Java 里。业务边界是：React 只面向用户交互，Java Spring Boot 负责业务状态、资料记录、权限边界和统一 `Result<T>` 响应，Python FastAPI 负责文档识别、递归切块、索引、混合检索和证据引用。这样后续替换向量库、embedding 模型或增加重排序模型时，不需要破坏 Java 业务接口。

日志记录是横切能力：Java 统一接收并写入 `log_event` / `log_error`，当前 RAG 使用 `domain=rag`，后续 Agent 编排、工具调用或长任务如果落地，可以复用同一套 `domain/module/stage/action/errorCode/contextJson` 结构。

### RAG 闭环与视频证据流程图

```mermaid
flowchart LR
    A["资料上传<br/>文档、图片、字幕、转写文本、原始视频<br/>长视频自动分片上传"] --> B["文档 / 视频解析<br/>MinerU、重叠音频分段 ASR、PPT 翻页检测、OCR"]
    B --> C["结构化切块<br/>标题、章节、页面、字幕时间段、片段摘要"]
    C --> D["个人知识库 RAG<br/>Multi-Query + BM25 + 1024 维向量 + RAG-Fusion"]
    D --> E["JD 分析<br/>按当前用户知识库检索岗位技能证据"]
    E --> F["输出结果<br/>已掌握 / 半掌握 / 缺口 / 学习计划 / 证据引用"]
```

### 视频 RAG 第一阶段

当前视频 RAG 已支持两类入口：一是 `.srt`、`.vtt` 和带时间戳的 `.txt` 字幕/转写文本；二是 `.mp4/.mov/.webm/.mkv/.avi` 等原始视频文件。原始视频会先由 Java 上传到配置的对象存储；大于 20MB 的视频由前端自动切成 20MB 分片，Java 合并后保存，再把 `sourcePath` 交给 Python 的视频源索引接口，避免再次把整段视频通过 multipart 转发。Python 会基于本地路径或公开视频 URL 执行 FFmpeg 抽音频、百炼 ASR 生成字幕、候选帧采样、PPT 翻页检测、关键帧 OCR 和视频片段摘要，最终把字幕 evidence、画面 OCR evidence 与片段摘要 evidence 统一写入 RAG。命中结果会保留 `startTime/endTime/playbackUrl`，前端知识库证据卡片展示命中时间范围，并提供“从这里播放”的跳转入口。

为避免 2-3 小时长视频在分段处切断关键连续内容，本项目的本地/私有视频 ASR 默认按 5 分钟音频段处理，并在每段前后保留 10 秒重叠窗口。ASR 识别时使用带重叠的上下文，入库时保留与该段名义时间范围相交的字幕，再按全局时间轴合并和去重。这样可以减少边界处半句话、公式解释或代码步骤被切断的问题，同时避免重复 evidence。

典型回答形态是：“某课程视频 `01:23:10-01:25:42` 命中字幕证据，同时可结合对应 PPT 翻页画面的 OCR 证据说明 RAG-Fusion 流程，点击证据卡片的播放入口跳到视频复习页定位。” 如果 FFmpeg、百炼 ASR、PPT 翻页检测、OCR 或片段摘要任一环节报错，Python 会把 `video.audio.extract`、`video.asr`、`video.frame.extract`、`video.slide_detect`、`video.frame_ocr[n]`、`video.segment_summary`、`video.fallback` 等位置写入 `parseQuality.messages`；Java 在 `PARTIAL` 时写入 `log_error.contextJson.errorLocation`，并保留可追踪的视频元数据 evidence。补配环境后可在资料列表触发“重建索引”或“高精度补跑”，重新生成字幕、画面 OCR 和片段摘要 evidence。

### 完整视频 RAG 技术路线

下面是原始视频 RAG 的完整业务流程。当前代码已覆盖上传保存、音频抽取、百炼 ASR、候选帧采样、PPT 翻页检测、关键帧 OCR、视频片段摘要、时间戳 evidence、错误位置日志和播放定位；仍不在本阶段启动 Agent 自主处理。

```mermaid
flowchart TD
    V0["上传视频或课程资料包"] --> V0A{"是否大视频"}
    V0A -->|"是"| V0B["前端 20MB 分片上传<br/>Java 暂存并按序合并"]
    V0A -->|"否"| V1["保存视频文件和元数据<br/>课程名、来源、用户、可见范围"]
    V0B --> V1
    V1 --> V1A["Python 视频源索引<br/>传 sourcePath，不转发整段视频字节"]
    V1A --> V2{"是否公开视频 URL"}
    V2 -->|"是"| V3["百炼 filetrans 异步 ASR<br/>输出句级时间戳"]
    V2 -->|"否"| V2A["FFmpeg 重叠音频分段<br/>默认 300s + 前后 10s 上下文"]
    V2A --> V3A["分段同步 ASR<br/>时间戳平移到全局时间轴"]
    V3A --> V3B["字幕合并去重<br/>保留相交字幕，重叠区做上下文"]
    V1 --> V4["候选帧采样<br/>默认每 5 秒抽一帧"]
    V4 --> V4A["PPT 翻页检测<br/>缩略图差异超过阈值保留为 ppt_flip"]
    V4A --> V5["百炼 Qwen-OCR / 本地 OCR<br/>识别板书、PPT、代码和图表文字"]
    V3 --> V6["字幕 / 转写文本解析<br/>生成 startTime、endTime"]
    V3B --> V6
    V5 --> V7["画面文字块<br/>绑定 frameTime 和 sourcePath"]
    V6 --> V8["视频片段摘要<br/>合并字幕要点和画面 OCR"]
    V7 --> V8
    V8 --> V9["结构化切块<br/>保留时间戳、章节、来源"]
    V2A -.报错.-> VERR["阶段告警<br/>parseQuality.messages 带 video.* 位置"]
    V3 -.报错.-> VERR
    V3A -.报错.-> VERR
    V4 -.报错.-> VERR
    V4A -.报错.-> VERR
    V5 -.报错.-> VERR
    V6 -.报错.-> VERR
    V8 -.报错.-> VERR
    VERR --> VLOG["Java 记录 RAG_INDEX_PARTIAL<br/>errorLocation 可定位报错环节"]
    V9 --> V10["个人知识库 RAG<br/>Multi-Query + BM25 + 1024 维向量 + RRF"]
    V10 --> V11["回答与证据引用<br/>视频时间段、字幕片段、画面 OCR、片段摘要"]
    V11 --> V12["播放定位<br/>videoUrl#t=秒 或视频复习页定位"]
```

技术选型：

- ASR：通过 `DASHSCOPE_API_KEY` 接入百炼语音识别模型；有公开视频 URL 时优先使用 `qwen3-asr-flash-filetrans` 异步文件转写获取句级时间戳，失败后降级为本地 FFmpeg 音频分段 + `qwen3-asr-flash` 同步转写；同步转写只返回纯文本时会按分段时长生成估算 SRT，再平移到原视频全局时间轴。
- 连续性保护：本地/私有视频默认按 `RAG_VIDEO_AUDIO_SEGMENT_SECONDS=300` 分段，并用 `RAG_VIDEO_AUDIO_OVERLAP_SECONDS=10` 给每段前后保留上下文。入库前保留与名义分段相交的字幕并合并去重，减少关键句子被边界切断。
- 关键帧和翻页：Python 先按候选帧间隔抽样，再用 Pillow 缩略图差异检测 PPT 翻页；首帧、翻页帧和固定间隔兜底帧会进入 OCR。
- 画面 OCR：沿用 Python RAG 内的百炼 Qwen-OCR；未配置或失败时只对可降级场景使用本地 OCR，不在 Java 中实现识别逻辑。每个 OCR 失败都会带 `video.frame_ocr[n]` 位置。
- 片段摘要：Python 将字幕 cue 与同时间段关键帧 OCR 合并为 `evidenceChannel=video_segment_summary` 的片段摘要块，再进入递归切块和索引。
- Embedding：统一使用百炼 `text-embedding-v4` 1024 维向量，pgvector 使用 HNSW + cosine。
- 检索：字幕、转写文本、关键帧 OCR、视频片段摘要、PPT/PDF OCR 和文档切块进入同一 RAG 仓库，查询时通过 Multi-Query、BM25、向量召回和 RRF/RAG-Fusion 融合排序。
- 播放定位：evidence 保留 `startTime/endTime/playbackUrl`；有真实视频地址时使用 `videoUrl#t=秒`，无真实视频地址时跳到视频复习页展示定位信息。
- 错误定位：Python 所有视频阶段告警写入 `parseQuality.messages`，Java 读取后在 `PARTIAL` 时写入 `log_error.contextJson.errorLocation`。
- 补跑修复：资料列表提供重建索引和高精度补跑入口，Java 会从本地上传目录或阿里 OSS 重新读取原始文件，再调用 Python 重建同一 `documentId` 的 RAG 索引。

### 整体业务流程图

下面的总图按当前代码实现整理：前端只访问 Java API；Java 负责登录用户、资料状态、对象存储、页面聚合、Agent 任务、记忆管理、简历模板业务、统一响应和日志；Python FastAPI 负责 RAG 计算、视频解析、检索重排、回答生成、JD 适配分析、Agent 执行、记忆索引检索和 DOCX 简历模板解析/补丁/导出。Agent 和简历模板均通过 Java 边界鉴权，不允许前端或 Agent 直连 Python 内部接口、数据库或对象存储。

```mermaid
flowchart TD
    U["用户登录并进入后台"] --> FE["React + Vite 前端<br/>工作台 / 学习资料 / 知识库 / JD 分析 / 简历适配 / 视频复习 / Agent 工作台 / 简历模板"]

    FE -->|"登录、当前用户"| AUTH["Java AuthController<br/>/api/auth/*"]
    AUTH --> AUTHDB["app_user / auth_session / auth_login_record"]

    FE -->|"上传文件/视频分片、粘贴文本、重建索引"| RAGAPI["Java RagController<br/>/api/rag/materials/*"]
    RAGAPI --> RAGSVC["RagService<br/>校验用户、资料归属和状态机"]
    RAGSVC --> STORE["ObjectStorageService<br/>本地 uploads 或阿里 OSS"]
    RAGSVC --> LMDB["learning_material<br/>PENDING / PARSING / REINDEXING"]
    RAGSVC --> PYIDX["Python 索引入口<br/>index-file / index-text / index-video-source"]
    STORE -->|"普通文件字节或视频 sourcePath"| PYIDX
    PYIDX --> TYPE{"资料类型判断"}
    TYPE -->|"原始视频"| VIDEOFLOW["视频处理链<br/>视频源读取 / 重叠音频分段 ASR / 候选帧采样 / PPT 翻页检测 / OCR / 片段摘要"]
    TYPE -->|"文档、图片、字幕、文本"| PARSE["多格式解析路由<br/>MinerU / 原生解析 / OCR / 字幕解析"]
    PARSE --> BLOCK["DocumentBlock 统一模型<br/>页码、幻灯片、sheet、字幕时间、播放地址"]
    VIDEOFLOW --> BLOCK
    VIDEOFLOW -->|"阶段 warning"| VMSG["parseQuality.messages<br/>video.audio.extract / frame_ocr[n] / segment_summary"]
    BLOCK --> CHUNK["递归切块 + 摘要索引<br/>标题、章节、段落、句子和原子块"]
    CHUNK --> RAGDB["PostgreSQL/pgvector<br/>rag_document / rag_chunk / BM25 / 1024 维向量"]
    RAGDB --> IDXRES["IndexResponse<br/>READY / PARTIAL / FAILED、parser、summary、chunkCount"]
    VMSG --> IDXRES
    IDXRES --> RAGSVC
    RAGSVC --> LMDB
    RAGSVC --> FE

    FE -->|"RAG 提问"| QUERYAPI["Java 查询接口<br/>/api/rag/query"]
    QUERYAPI -->|"覆盖 userId 与 visibilityScope"| PYQ["Python 查询入口<br/>/internal/rag/query"]
    PYQ --> RET["Multi-Query<br/>BM25 + pgvector 召回<br/>RRF/RAG-Fusion + rerank"]
    RET --> ANS["百炼 LLM 或本地规则化降级<br/>只基于 evidence 生成回答"]
    ANS --> QRES["RagQueryVO<br/>answer、expandedQueries、evidences、diagnostics"]
    QRES --> FE

    FE -->|"查看单个资料 evidence"| EVAPI["Java evidence 接口<br/>/api/rag/materials/{id}/evidences"]
    EVAPI --> PYE["Python evidence 列表<br/>/internal/rag/documents/{document_id}/evidences"]
    PYE --> FE

    FE -->|"提交 JD + 简历文本"| JDAPI["Java PageDataController<br/>/api/page-data/jd-analysis/analyze"]
    JDAPI --> PYJD["Python JD 分析<br/>/internal/rag/jd-analysis"]
    PYJD --> JDR["技能抽取 + 用户知识库检索<br/>supported / weak / missing"]
    JDR --> JDTABLE["jd_analysis_report / jd_analysis_skill<br/>jd_learning_plan_item / resume_evidence_alignment"]
    JDTABLE --> FE

    FE -->|"Agent 任务、审批、撤销和记忆管理"| AGAPI["Java AgentController / AgentMemoryController<br/>/api/agent/*"]
    AGAPI --> AGSVC["AgentService / AgentMemoryService / ToolGateway<br/>用户归属、审批状态、幂等键和审计"]
    AGSVC --> AGDB["agent_task / agent_tool_call / agent_operation<br/>agent_memory_item / agent_memory_version / agent_memory_audit"]
    AGSVC -->|"内部 token"| PYAG["Python Agent / Memory Service<br/>LangGraph、记忆预取、候选提炼、索引检索"]
    PYAG --> AGSVC
    AGSVC -->|"只读工具和变更工具复用业务能力"| RAGSVC
    AGSVC --> FE

    FE -->|"简历模板上传、预览确认、区域约束、补丁和导出"| TPLAPI["Java 简历模板接口<br/>/api/rag/resume/templates/*"]
    TPLAPI --> TPLSVC["RagService 模板子流程<br/>DOCX 校验、用户归属、文件资源和版本"]
    TPLSVC --> STORE
    TPLSVC -->|"内部 token"| PYTPL["Python 简历模板接口<br/>内部模板抽取 / 图片预览 / JSON Patch / Layout Guard / DOCX 导出"]
    PYTPL --> TPLSVC
    TPLSVC --> TPLDB["resume_template / resume_template_field<br/>preview_page / region_annotation / patch_draft / export"]
    TPLSVC --> FE

    FE -->|"工作台、简历适配、视频复习、设置"| PAGEAPI["Java PageDataController<br/>/api/page-data/*"]
    PAGEAPI --> APPDB["业务库聚合<br/>资料、视频切片、JD 报告、简历证据、系统设置"]
    APPDB --> FE

    RAGAPI --> LOG["LogService<br/>domain=rag"]
    QUERYAPI --> LOG
    EVAPI --> LOG
    JDAPI --> LOG
    AGAPI --> LOG
    TPLAPI --> LOG
    IDXRES -->|"PARTIAL/FAILED + errorLocation"| LOG
    LOG --> LOGDB["log_event / log_error<br/>stage、action、errorCode、contextJson"]

    FE --> UI["前端展示<br/>资料状态、证据卡片、视频播放定位、JD 匹配矩阵、学习计划、Agent 执行轨迹、记忆列表和模板预览"]
```

### 第二阶段 Agent 能力现状与后续边界（阶段 0-7 已实现）

本节记录当前已经落地的 Agent 能力和仍保留的后续边界。阶段 0 已新增 [Agent 接口契约](docs/api/agent.md) 和 `agent_*` 表结构；阶段 1 已实现 Java `agent_task` 创建/查询、HTTP 级 `/api/internal/agent/tools/read` 只读 Tool Gateway、严格内部 token 校验和 `RagService.queryNonPersistent()`；阶段 2 已实现 Python LangGraph 纯只读闭环、Java 调 Python Agent client、Python 回写 Java events，以及前端 `/agent` 页面；阶段 3 已实现规划类 `planning_task` 的计划审批、只读 evidence 对齐、能力缺口分析和输出审批；阶段 4 已实现 Agent 自身范围内的 `resume_revision_save`、`jd_learning_plan_save`、`agent_task_cancel_request` 变更网关、CRUD 审批、before/after snapshot、幂等键、撤销窗口和前端撤销入口；阶段 5 已实现 `web_search_probe` 联网参考工具，用于补充公司背景、技能趋势和外部学习资源，外部结果不写入 RAG evidence 库；阶段 6 已补齐简历模板上传、服务端内部模板抽取、图片预览、区域标注、补丁草稿、校验和导出链路，前端不展示 Agent/Python 提取出的模板字段明细，旧 `resume_template_fill` 仅保留为兼容型确定性填充能力；阶段 7 已实现 Agent 记忆最小闭环，包括当前用户记忆 CRUD、待审确认/拒绝、归档/删除、版本和审计、Python 记忆候选提炼、索引/检索、Agent 任务 `memoryContext` 预取和前端记忆管理。

当前仍未实现 MCP、`web_page_fetcher`、自主长任务调度、多 Agent 协作，也未把资料重建索引、普通上传、分片上传或确定性 RAG 入库纳入 Agent 工具。已落地的 Agent 编排继续遵守边界：React 只调用 Java；Java 负责登录用户、权限、业务状态、资料记录、统一响应和日志；Python 负责 Agent 图执行、RAG 计算、解析、索引、检索、重排、回答和 evidence 引用。Agent 不直接绕过 Java 调用 Python 内部接口、数据库或对象存储。

已落地的 Agent 编排统一收敛到 Python `pae_react_graph`：规划器采用 PAE（Plan-and-Execute）生成计划、完成标准和工具范围；执行器采用 ReAct 式“行动 -> 观察 -> 修正”选择下一步工具；工具节点只负责适配 Java Tool Gateway，后续 MCP 也从这里接入。普通文件上传、分片上传和确定性入库不纳入 Agent 工具链，仍由 React 调用 Java 的现有资料接口完成。Agent 只在需要规划、观察、联网参考、evidence 对齐、JD/简历匹配、草稿生成、记忆提炼或人工审批的场景介入。工具注册面以 Java 业务能力为准；当前 Agent 执行器已使用 LangGraph/Python 实现，但只能通过 Java Tool Gateway 触发业务动作，由 Java 负责用户隔离、权限、状态机、日志、幂等和错误映射。

查询和只读工具不需要 Human-in-the-Loop，但必须由 Java 从登录 token 解析当前用户，并在 Tool Gateway 或业务服务层强制资源归属过滤：当前只能查询当前用户自己上传的数据，不能读取其他用户上传的资料。`explicitGrant(currentUserId, resourceId)` 是后续授权表的预留语义，未落表前非 owner 资源一律拒绝；Agent 不接收、也不自行构造跨用户资源过滤条件。Create/Update/Delete 以及任何业务状态变更必须进入 `human_crud_review` 做具体操作审批。计划确认只批准任务目标和工具路线，不等于批准任何写操作。自动写入 `log_event/log_error` 属于平台审计和故障观测副作用，不作为 Agent 可选工具，也不因工具审批而阻断；但日志必须由服务端补齐当前 `userId`、`traceId`，脱敏后写入，不能记录简历正文、资料正文或模型密钥。

```mermaid
flowchart TD
    START["Start<br/>Java 创建 agent_task 并调用 Python"] --> M0["记忆预取 1<br/>用户偏好、历史约束、近期任务"]
    M0 --> PL["规划器 Planner<br/>PAE：计划、完成标准、工具范围"]
    PL --> HPLAN{"是否需要计划确认"}
    HPLAN -->|"是"| PLAN_REVIEW["HITL：PLAN 审批<br/>只确认目标和路线"]
    HPLAN -->|"否"| M1["记忆预取 2<br/>基于计划和工具意图检索任务记忆"]
    PLAN_REVIEW -->|"批准"| M1
    PLAN_REVIEW -->|"拒绝或要求修改"| ANSWER["回答节点<br/>组织当前状态和可执行建议"]

    M1 --> EXEC["执行器 Executor<br/>ReAct：选择下一步行动"]
    EXEC --> TOOL["工具节点 Tool Adapter<br/>Java Gateway / 后续 MCP"]
    TOOL --> TOOL_RESULT{"工具结果"}

    TOOL_RESULT -->|"失败"| REPAIR["修补节点<br/>重试、跳过、重规划或汇报无法完成"]
    REPAIR -->|"重试"| TOOL
    REPAIR -->|"重规划"| PL
    REPAIR -->|"无法完成"| ACCEPT["验收器<br/>检查计划完成度和失败原因"]

    TOOL_RESULT -->|"成功"| ACCEPT
    ACCEPT -->|"计划未完成"| EXEC
    ACCEPT -->|"需补救"| REPAIR
    ACCEPT -->|"验收通过"| ANSWER

    ANSWER --> DONE["先完成用户响应<br/>TASK_COMPLETED / WAITING_OUTPUT_REVIEW / FAILED"]
    ANSWER -. "后台或显式触发" .-> MEM["长期记忆整理<br/>候选提炼、冲突检测、版本建议"]
    MEM --> MEM_GATE{"是否允许写入"}
    MEM_GATE -->|"仅推断候选"| PENDING["PENDING_REVIEW<br/>不自动激活"]
    MEM_GATE -->|"显式记住或用户确认"| MEM_REVIEW["HITL：MEMORY_WRITE / CRUD"]
    MEM_REVIEW -->|"批准"| SAVE["Java Mutation Gateway<br/>agent_memory_candidate_save"]
```

当前 LangGraph 按 `docs/api/agent.md` 契约落地：`pae_react_graph` 已统一纯只读和规划类路径，固定节点为 `memory_prefetch_before_planner -> planner -> plan_review? -> memory_prefetch_after_planner -> executor -> tool_adapter -> repair/acceptance -> answer_writer -> post_answer_memory`。`human_plan_review` 只确认复杂任务目标和工具路线，普通 RAG 查询、资料状态读取、evidence 读取和检索覆盖诊断不经过计划审批；`human_crud_review` 才批准具体保存类变更。撤销窗口当前通过 Java `POST /api/agent/operations/:id/undo` 暴露给前端，不作为 Python 可直接调用的 Tool Gateway 工具。

PAE 主图不直接改写 DOCX，也不直接写对象存储。若 Planner 发现计划中包含“基于模板改写简历、保留原版式、导出简历”等步骤，执行器只把该步骤路由到专门的 `resume_rewrite_graph` 子图；子图完成后把补丁草稿、版式审计、预览状态和待审批动作作为 Observation 回到主图，再由主图继续验收、输出或等待用户确认。

```mermaid
flowchart TD
    START["用户发起 Agent 任务<br/>JD、简历目标、模板选择"] --> PAE["pae_react_graph 主图"]
    PAE --> M0["记忆预取<br/>偏好、历史约束、近期任务"]
    M0 --> PL["PAE Planner<br/>识别计划步骤和工具范围"]
    PL --> GATE{"计划是否包含<br/>简历改写步骤"}

    GATE -->|"否"| EXEC["ReAct Executor<br/>普通工具执行循环"]
    GATE -->|"是"| SUBROUTE["Tool Adapter<br/>路由到简历改写子图"]
    SUBROUTE --> SUB["resume_rewrite_graph 子图<br/>模板抽取、补丁、Layout Guard、预览"]
    SUB --> OBS["子图 Observation<br/>补丁草稿、版式审计、导出候选、审批需求"]

    EXEC --> READ["Java Read Tool Gateway<br/>RAG、资料、evidence、记忆"]
    EXEC --> MUTATION["human_crud_review<br/>Java Mutation Gateway"]
    READ --> ACCEPT["主图验收节点<br/>检查计划完成度"]
    MUTATION --> ACCEPT
    OBS --> ACCEPT
    ACCEPT -->|"计划未完成"| EXEC
    ACCEPT -->|"需要用户确认"| REVIEW["PLAN / OUTPUT / CRUD 审批"]
    ACCEPT -->|"通过"| ANSWER["回答节点<br/>返回状态、预览和下一步"]
    REVIEW -->|"批准后继续"| EXEC
    REVIEW -->|"拒绝或修改"| ANSWER
```

简历改写子图的职责是把“内容改写”和“版式保护”拆开：LLM 只生成字段级内容补丁和显式授权的版式变化请求，确定性 DOCX 应用器负责写入，Layout Guard 负责判断是否发生未授权的样式/结构变化。用户主动要求新增段落、删减内容、加粗字段或做局部重排时，不会因为版式发生变化就直接打回；这些变化必须进入 `LayoutChangeContract`，只有超出授权范围的变化才回到模板抽取/补丁生成环节重新处理。

```mermaid
flowchart TD
    A["Start: resume_rewrite_graph<br/>PAE 主图传入简历改写步骤"] --> B["输入归一化<br/>JD、目标岗位、模板ID、用户指令、版式契约"]
    B --> C{"原始文件类型"}
    C -->|"DOCX"| D["读取受控 DOCX<br/>保留原文件和版本"]
    C -->|"PDF"| E["PDF 转 DOCX 候选<br/>生成转换质量报告"]
    E --> EQ{"转换质量是否可接受"}
    EQ -->|"否"| ESTOP["等待用户上传 DOCX<br/>或确认转换结果"]
    EQ -->|"是"| D

    D --> F["模板抽取节点<br/>内部字段、样式指纹、结构指纹、可编辑区域"]
    F --> R["证据检索节点<br/>JD 要求、简历摘要、RAG evidence"]
    R --> H["补丁生成节点<br/>严格 JSON 字段级内容补丁"]
    H --> LC["LayoutChangeContract 合并<br/>PRESERVE_LAYOUT / CONTROLLED_EDIT / RELAYOUT"]
    LC --> I["确定性应用节点<br/>只按契约修改 DOCX"]
    I --> J["Layout Guard 检查节点<br/>结构指纹、样式差异、文本容量、渲染预览"]
    J -->|"发现未授权变化"| F
    J -->|"授权变化通过"| K["验收节点<br/>JD 覆盖、证据引用、风险标记"]
    K -->|"不通过"| R
    K -->|"通过"| L["预览与用户确认<br/>前端只展示预览和确认信息"]
    L -->|"确认导出"| M["Java 保存 OSS/对象存储<br/>返回下载链接"]
    L -->|"要求调整"| R
```

Layout Guard 的检查目标不是“完全没有任何变化”，而是“没有未授权变化”。`PRESERVE_LAYOUT` 模式下重点检查页数、段落/表格/列表结构、字体、字号、加粗、颜色、间距、页边距、图片和渲染像素差异；`CONTROLLED_EDIT` 模式允许 `allowedChanges` 中声明的段落增删、字段加粗、局部文本容量变化；`RELAYOUT` 模式必须把风险提示和预览交给用户确认后再导出。模板抽取结果、字段原文、定位和样式指纹只在服务端内部流转，React 页面不展示这些细节。

固定图节点与 guardrail 不作为 Planner 可选工具，包括 `auth_context_resolver`、`scope_ownership_guard`、`intent_router`、Java/API 输入校验、受权限控制的上下文加载、`tool_router`、`java_read_tool_gateway`、`human_plan_review`、`human_crud_review`、`privacy_guard`、`citation_guard`、`undo_window_manager` 和 `audit_logging`。只读工具不需要人工审批，但必须通过 `java_read_tool_gateway` 强制当前用户归属过滤；`utc_time_provider` 为纯系统工具，不访问用户数据。当前网关只允许服务端计算 `ownerUserId == currentUserId`，`explicitGrant` 是预留能力，禁止 Agent 自行传入跨用户过滤条件。`rag_query_probe_non_persistent` 不写 `rag_query_history`，只允许写脱敏审计日志；如果未来要保存查询历史，则必须按变更工具审批。`retrieval_coverage_probe` 只读取 Java 查询诊断里的 `expandedQueries`、候选数量、evidence 分布和覆盖率，不重新实现 Multi-Query、BM25、向量召回或 RAG-Fusion。

| 工具名 | 类型 | 何时调用 | 是否需要 HITL | Java 边界与权限约束 | 持久化副作用 |
| --- | --- | --- | --- | --- | --- |
| `material_status_reader` | 只读 | 用户查看资料解析、索引、失败原因或重试建议时 | 否 | 只走 `java_read_tool_gateway`，当前仅本人资料 | 无 |
| `material_evidence_reader` | 只读 | 需要列出资料标题、章节、片段、来源和分数时 | 否 | 只走 `java_read_tool_gateway`，当前仅本人 evidence | 无 |
| `material_preview_reader` | 只读 | 需要受限预览片段帮助判断是否补跑解析时 | 否 | 只走 `java_read_tool_gateway`，预览长度和字段由 Java 控制 | 无 |
| `rag_query_probe_non_persistent` | 只读 | 普通 RAG 问答或 JD/简历适配需要临时检索时 | 否 | 只走 Java 查询能力，当前强制 owner 范围 | 不写查询历史，仅写脱敏审计日志 |
| `retrieval_coverage_probe` | 只读 | evidence 覆盖不足，需要查看召回分布和覆盖率时 | 否 | 只读取 Java 返回的诊断摘要，不接触原始跨用户数据 | 无 |
| `evidence_quality_auditor` | 只读 | 输出前检查引用完整性、证据充分性和风险等级时 | 否 | 只走 `java_read_tool_gateway`，只处理已授权 evidence 和工具结果 | 无 |
| `resume_evidence_aligner` | 只读生成 | JD/简历适配时对齐 supported / weak / missing 证据 | 否 | 只走 `java_read_tool_gateway`，只读取当前用户简历、资料和授权 JD/资料 | 无，产出草稿 Observation |
| `gap_analyzer` | 只读生成 | 根据 evidence 缺口生成能力差距和学习建议时 | 否 | 只走 `java_read_tool_gateway`，只处理已授权 evidence 和 JD 要求 | 无，产出草稿 Observation |
| `utc_time_provider` | 只读 | Agent 需要获取当前 UTC 时间以判断技能、资料、证书时效性时 | 否 | 纯系统时间读取，不涉及用户数据 | 无 |
| `web_search_probe` | 只读 | 需要联网参考公司背景、技能趋势或外部学习资源时 | 否 | 只走 Java Tool Gateway，由 Java 调 Tavily，未配置密钥时返回可重试错误 | 无，外部结果不写入 RAG evidence |
| `agent_memory_retriever` | 只读 | Agent 任务启动或执行前需要读取当前用户可注入记忆时 | 否 | 只走 Java Tool Gateway，Java 负责用户归属和 ACTIVE 状态二次过滤 | 无，只返回 `memoryContext` |
| `agent_memory_candidate_proposer` | 只读生成 | 任务完成后需要提炼待确认记忆候选时 | 否 | Python 只做候选和冲突判断，Java 保存前做用户归属、去重和脱敏审计 | 无，候选默认不激活 |
| `resume_revision_save` | 变更 | 用户确认保存简历改写版本后 | 是，`human_crud_review` | 只走 Java Tool Gateway，校验当前用户简历归属 | 保存草稿/版本，可撤销 |
| `jd_learning_plan_save` | 变更 | 用户确认保存学习路线或计划后 | 是，`human_crud_review` | 只走 Java Tool Gateway，校验当前用户计划归属 | 保存计划，可撤销 |
| `agent_task_cancel_request` | 变更 | 用户显式取消 Agent 任务或审批取消时 | 是，`human_crud_review` | 只走 Java Tool Gateway，只能取消本人任务 | 更新任务状态 |
| `agent_memory_candidate_save` | 变更 | 用户确认保存记忆候选或显式记住一条偏好时 | 是，`human_crud_review` | 只走 Java Tool Gateway，保存为当前用户记忆并触发 Python 索引 | 写 `agent_memory_item`、版本/审计和索引 |

当前已暴露的变更工具包括 `resume_revision_save`、`jd_learning_plan_save`、`agent_task_cancel_request` 和 `agent_memory_candidate_save`。这些工具全部必须经过 `human_crud_review`、Java Tool Gateway 归属校验、幂等键校验、操作日志和撤销窗口；撤销操作由前端显式调用 Java `POST /api/agent/operations/:id/undo`，不是 Python Agent 可自行选择的工具。`material_reindex_request` 仍是后续资料重建链路规划，当前普通上传、分片上传、重建索引和确定性 RAG 入库继续走现有 RAG 接口。`jd_skill_extractor` 与 `resume_parser` 先作为 JD/简历分析服务内部步骤，不作为普通 Tool，除非未来确实需要 Planner 选择、重试或跳过。

变更操作的撤销窗口状态建议为：`PENDING_APPROVAL -> APPLIED_UNDOABLE -> UNDONE / UNDO_EXPIRED / FINALIZED / FAILED`。mutation 执行前必须生成 `beforeSnapshotRef`，执行后生成 `afterSnapshotRef/auditEventId/undoDeadline`。撤销只能恢复业务记录、草稿状态、计划记录或旧索引引用；模型调用成本、已消耗的长视频处理资源不可撤销，只能在审批时明确 `costLevel` 和不可逆部分。

所有工具输出都应结构化，字段至少包括 `toolName/taskId/operationType/resourceType/resourceId/status/message/evidenceIds/riskLevel/nextActionCandidates/errorCode/retryable/requiresApproval/approvalType/costLevel/idempotencyKey/diagnostics/ownershipVerified/scope/beforeSnapshotRef/afterSnapshotRef/undoDeadline/undoable/auditEventId`。`ownerUserId` 仅服务端内部使用，不给普通前端展示；前端只看 `ownershipVerified=true` 和 `scope=current_user_or_authorized`。生成结论类工具必须带 evidence 或明确返回缺证据，不能编造项目经历。

### 细分 RAG 流程图

这里把 RAG 拆成“索引流程”“查询流程”“检索流程”三张图。查询流程解决“用户问题如何进入 RAG 并形成响应”，检索流程解决“候选切块如何被召回、融合、重排并变成证据”。Java 只承载业务状态和统一响应，Python 承载 RAG 计算。

#### 索引流程图：资料到可检索证据

```mermaid
flowchart TD
    A0["用户提交学习资料<br/>文件、文本或长视频"] --> A1{"是否大视频"}
    A1 -->|"是"| A1A["React 分片上传<br/>默认每片 20MB"]
    A1 -->|"否"| A1B["React 提交普通表单<br/>标题、类型、来源、可见范围"]
    A1A --> A2["Java RAG 控制器<br/>校验空内容、分片参数、资料类型"]
    A1B --> A2
    A2 --> A3["RAG 业务服务<br/>生成资料ID，状态写为 PENDING"]
    A3 --> A4["MyBatis 持久层<br/>保存资料记录"]
    A3 --> A5["Python 服务调用客户端<br/>普通文件转发字节，视频传 sourcePath"]
    A5 --> A6{"输入是文件还是文本"}
    A6 -->|"文件"| A7["按格式选择解析器<br/>PDF / DOCX / PPTX / MD / XLSX / 图片 / TXT / 视频"]
    A7 --> AV0{"是否原始视频文件"}
    AV0 -->|"是"| AV1["视频源处理<br/>公开视频 filetrans 或本地重叠音频分段"]
    AV1 --> AV1A["字幕全局时间轴合并<br/>重叠上下文防切断，重复 cue 去重"]
    AV1A --> AV2["候选帧采样<br/>PPT 翻页检测保留关键帧"]
    AV2 --> AV3["关键帧 OCR 入库<br/>evidenceChannel=frame_ocr"]
    AV3 --> AV4["视频片段摘要<br/>evidenceChannel=video_segment_summary"]
    AV4 --> A10
    AV1 -.报错.-> AVE["阶段告警<br/>video.audio.extract / video.asr / video.frame_ocr[n] / video.segment_summary / video.fallback"]
    AV2 -.报错.-> AVE
    AV3 -.报错.-> AVE
    AV4 -.报错.-> AVE
    AVE --> A18
    AV0 -->|"否"| A8{"原生解析质量是否足够"}
    A8 -->|"否或高精度"| A9["LibreOffice 转 PDF<br/>补跑 MinerU / OCR"]
    A8 -->|"是"| A10["统一 DocumentBlock<br/>保留结构和来源定位"]
    A9 --> A10
    A6 -->|"文本"| A10
    A10 --> A11["递归切块<br/>标题 -> 章节 -> 页面/幻灯片 -> 段落 -> 句子"]
    A11 --> A12["表格、图片、代码块原子保存<br/>文本块使用重叠窗口"]
    A12 --> A13["补齐 evidence 元数据<br/>资料ID、blockId、来源、解析器、位置"]
    A13 --> A14["摘要索引<br/>资料摘要 + 章节摘要"]
    A14 --> A15["关键词索引<br/>分词、词频、文档频率"]
    A14 --> A16["pgvector 向量索引<br/>真实 PostgreSQL 向量列"]
    A15 --> A17["PostgreSQL/pgvector 检索仓库<br/>资料、切块、词项统计、向量"]
    A16 --> A17
    A17 --> A18["Python 返回解析入库结果<br/>READY / PARTIAL / FAILED<br/>parseQuality.messages 保留报错位置"]
    A18 --> A19["Java 回写资料状态<br/>完成、部分完成或失败"]
    A18 -->|"PARTIAL 且有 messages"| A19E["Java 写入 log_error<br/>errorLocation 指向具体视频阶段"]
    A19E --> A20
    A19 --> A20["前端刷新资料列表<br/>展示状态、切块数、摘要或错误"]
```

#### 查询流程图：用户问题到响应封装

```mermaid
flowchart TD
    B0["用户在工作台或知识库提问"] --> B1["React 发送 RAG 请求<br/>问题、返回数量、元数据过滤条件"]
    B1 --> B2["Java 查询接口<br/>校验问题、数量和过滤条件"]
    B2 --> B3["Java 统一业务边界<br/>不直接执行智能检索逻辑"]
    B3 --> B4["Python 查询入口<br/>接收问题和过滤条件"]
    B4 --> B5["查询规范化<br/>去除空白、保留原问题、限制返回数量"]
    B5 --> B6["Multi-Query 扩展<br/>原问题、关键证据、学习资料、JD 或简历变体"]
    B6 --> B7["生成检索任务<br/>扩展问题列表 + 元数据过滤条件 + 返回数量"]
    B7 --> B8["进入检索流程<br/>执行召回、融合、重排"]
    B8 --> B9{"是否命中证据"}
    B9 -->|"否"| B10["空结果响应<br/>提示先上传资料或放宽过滤条件"]
    B9 -->|"是"| B11["后检索重排<br/>百炼 qwen3-rerank 或本地确定性重排"]
    B11 --> B12["回答生成策略选择"]
    B12 -->|"已配置百炼 Key"| B13["百炼 LLM 提示词组装<br/>问题 + evidence + 引用约束"]
    B12 -->|"未配置或调用失败"| B14["本地规则化降级回答<br/>保留 evidenceId 引用"]
    B13 --> B15["带引用答案<br/>只基于 evidence 回答并保留出处"]
    B14 --> B15
    B15 --> B16["程序化追加证据引用摘要<br/>资料、章节或视频时间、来源、分数"]
    B16 --> B17["Python 组装查询响应<br/>回答、扩展问题、证据、诊断信息"]
    B10 --> B17
    B17 --> B18["Java 统一响应封装<br/>成功结果或错误映射"]
    B18 --> B19{"是否包含视频 evidence"}
    B19 -->|"是"| B20["React 展示视频证据卡片<br/>字幕、画面 OCR、片段摘要、startTime / endTime / playbackUrl"]
    B19 -->|"否"| B21["React 展示普通证据卡片<br/>来源、章节、页码、分数"]
```

#### 检索流程图：召回、融合、重排到证据

```mermaid
flowchart TD
    C0["检索任务输入<br/>扩展问题列表、过滤条件、返回数量"] --> C1["读取 PostgreSQL/pgvector 仓库<br/>资料、切块、词频、文档频率、向量"]
    C1 --> C2["按元数据过滤候选切块<br/>用户、可见范围、资料类型、来源、章节"]
    C2 --> C3{"过滤后是否还有切块"}
    C3 -->|"否"| C4["返回空证据列表<br/>记录过滤后数量为 0"]
    C3 -->|"是"| C5["遍历每一个扩展问题"]
    C5 --> C6["分词处理<br/>中文单字 + 英文术语 + 数字符号"]
    C6 --> C7["BM25 关键词召回<br/>计算词频、逆文档频率、长度归一"]
    C6 --> C8["pgvector 向量召回<br/>按余弦距离排序"]
    C7 --> C9["关键词排名列表<br/>适合精确术语和岗位关键词"]
    C8 --> C10["语义排名列表<br/>适合同义表达和上下文近似"]
    C9 --> C11["汇总多路排名列表<br/>每个扩展问题保留候选结果"]
    C10 --> C11
    C11 --> C12["RRF / RAG-Fusion 融合<br/>按 1/(60 + 排名) 累加得分"]
    C12 --> C13["去重并重新排序<br/>同一切块只保留一个融合分数"]
    C13 --> C14["保留 topK*3 候选证据<br/>为后检索重排留出候选池"]
    C14 --> C15["百炼 qwen3-rerank<br/>不可用时本地确定性重排"]
    C15 --> C16["按返回数量截取证据<br/>形成最终 evidence 列表"]
    C16 --> C17["证据字段构造<br/>证据ID、资料ID、标题、片段、来源、章节、类型、分数"]
    C17 --> C18["片段压缩<br/>清理空白，超过 220 字符则截断"]
    C18 --> C19["返回证据列表和诊断信息<br/>扩展问题、过滤后切块数量、rerank provider"]
```

### 索引阶段：把学习资料变成可检索证据

1. 用户在前端上传文件，或在“学习资料”页面粘贴文本笔记。
2. 前端只调用 Java API：文件走 `/api/rag/materials/upload`，文本走 `/api/rag/materials/text`。
3. Java 在 `learning_material` 中先创建资料记录，状态从 `PENDING` 进入 `PARSING`，Python 返回后更新为 `READY`、`PARTIAL` 或 `FAILED`，用于前端展示处理状态和最近资料列表。
4. Java 通过 `PythonRagClient` 调 Python 内部接口。文本会被包装为 `documentId/title/documentType/source/userId/visibilityScope/content`；文件会用 `multipart/form-data` 转发给 Python。
5. Python 文件入口按格式选择解析器：PDF 优先 MinerU；DOCX/PPTX/XLSX/Markdown/TXT 优先原生结构解析；DOC/PPT 通过 LibreOffice 转换后解析；图片走 OCR。
6. Python 将所有解析结果统一为 `DocumentBlock`，保留 block 类型、页码、幻灯片、sheet、cell range、来源路径、解析器和置信度。
7. 对 DOCX/PPTX/XLSX 等结构化文件计算解析质量；低置信、截图型或高精度模式时，补跑 PDF + MinerU/OCR。
8. Python 使用递归切块器做切块，优先保留标题、章节、页面、幻灯片、段落和句子结构；表格、图片、代码块、公式和图表默认作为原子块。
9. 每个切块都保留 evidence 元数据：资料 ID、标题、类型、来源、用户、可见范围、blockId、blockType、位置、解析器、来源路径和置信度。
10. 摘要索引组件生成文档级摘要和章节级摘要；同时把每个切块的 BM25 词项统计、元数据和向量写入 PostgreSQL/pgvector。
11. Python 返回 `READY/PARTIAL/FAILED`、切块数量、解析器和摘要；Java 更新资料记录，前端展示解析状态、切块数和摘要。

### 查询阶段：把问题变成带证据引用的回答

1. 用户在工作台或知识库页面输入问题。
2. 前端调用 Java `/api/rag/query`，Java 不做检索逻辑，只做统一接口和错误边界，然后调用 Python `/internal/rag/query`。
3. Python 先做 Multi-Query 扩展：保留原问题，再补充“关键证据”“学习资料/笔记”等查询变体；如果问题包含 JD、岗位、简历、项目等词，会补充更贴近岗位适配或简历证据的查询变体。
4. Python 按元数据过滤条件过滤候选切块。当前登录态由 Java `/api/auth/login` 处理，前端请求自动携带 Bearer Token；Java 将当前用户 ID 写入资料记录、Python 索引 metadata 和查询 `metadataFilter.userId`，默认管理员账号为 `admin / 123456`。
5. 每个查询变体同时走两路召回：BM25 负责关键词精确匹配，pgvector 负责向量相似度召回。
6. 多个查询变体、多个召回器的结果通过 RRF 做 RAG-Fusion 融合排序，避免单一路径漏召回。
7. 系统先保留 RAG-Fusion 候选证据，再通过百炼 `qwen3-rerank` 或本地确定性重排截取最终 evidence，并返回证据 ID、资料 ID、标题、片段、来源、章节、资料类型和分数。
8. 回答生成优先使用百炼 LLM 的 evidence 约束提示词，要求只基于证据回答并保留 `[evidenceId]` 引用；未配置 Key、调用失败或测试环境会降级为本地规则化摘要。

### 当前实现边界

- 当前 Python RAG 正式存储使用 PostgreSQL/pgvector，`rag_document` 保存资料摘要，`rag_chunk` 保存递归切块、元数据、BM25 词项统计和 `VECTOR(1024)` 向量，HNSW 索引使用 cosine 距离。
- 当前向量生成使用阿里云百炼 / DashScope `text-embedding-v4`，默认 1024 维，统一通过 `DASHSCOPE_API_KEY` 调用；单元测试可通过 `RAG_EMBEDDING_PROVIDER=hash` 使用离线确定性向量，生产环境不建议使用 hash provider。
- 当前 OCR 优先使用百炼 Qwen-OCR，未配置或失败时降级 `pytesseract`；Embedding 与 OCR 都收敛在 Python RAG 服务内，Java 不持有模型 Key。
- 当前视频 RAG 支持字幕 / ASR 转写文本，也支持原始视频经过 FFmpeg + 百炼 ASR、候选帧采样、PPT 翻页检测、关键帧 OCR 和视频片段摘要后入库；时间戳证据通过 `startTime/endTime/playbackUrl` 返回。
- 当前回答生成已接入百炼 LLM 的 evidence 约束回答，未配置 Key 或测试环境会降级为规则化摘要；无证据时拒答并提示补充资料。
- 当前 Agent 任务只保留页面入口，不实现自主规划、工具调用或长任务编排。

## 目录结构

```text
frontend-react/   React 前端，复刻 Stitch 生成的工作台风格并绑定路由
backend-java/     Java Spring Boot API，Controller + Service + Mapper
ai-python/        Python FastAPI RAG 服务，负责解析、切块、索引和检索
docs/             API、架构、Stitch 页面提取记录
infra/sql/        数据库初始化 SQL 与增量迁移
samples/          示例 JD、简历和学习资料
```

## 本地启动

Python RAG 服务：

```powershell
cd ai-python
conda env create -f environment.yml
conda activate learning-evidence-rag
$env:PYTHONPATH='.'
$env:RAG_STORE_BACKEND='pgvector'
$env:RAG_DATABASE_URL='postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic'
$env:RAG_DATABASE_SCHEMA='learning_evidence'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAG_EMBEDDING_MODEL='text-embedding-v4'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

已创建过环境时，使用 `conda env update -f ai-python/environment.yml --prune` 同步依赖即可。`ai-python/requirements.txt` 保留为 pip 兼容依赖清单，正式本地开发以 Conda 环境为准。

`environment.yml` 会安装视频解析需要的 `ffmpeg/ffprobe`、本地 OCR 降级需要的 `tesseract`、以及 Python 侧解析依赖。`OCR_LANG=chi_sim+eng` 还要求 Tesseract 环境中存在对应语言数据；如果本机没有中文语言包，可以先改成 `eng` 验证链路，或安装中文 traineddata 后再恢复。MinerU 和 LibreOffice 仍按外部可选命令接入：需要高精度 PDF 识别时配置 `MINERU_COMMAND`，需要 `.doc/.ppt` 或 DOCX/PPTX 转 PDF 补充解析时配置 `LIBREOFFICE_COMMAND` / `SOFFICE_COMMAND`。

PostgreSQL/pgvector 建库和向量仓库创建语句见 [docs/database/postgresql-pgvector.md](docs/database/postgresql-pgvector.md)。完整初始化 SQL 在 [infra/sql/init.sql](infra/sql/init.sql)，生产 RAG 表增量迁移 SQL 在 [infra/sql/alter-database/20260616_0200_create_pgvector_rag_store.sql](infra/sql/alter-database/20260616_0200_create_pgvector_rag_store.sql)，Ragas 同库评估表增量迁移 SQL 在 [infra/sql/alter-database/20260621_0100_create_ragas_test_pgvector_store.sql](infra/sql/alter-database/20260621_0100_create_ragas_test_pgvector_store.sql)，Agent 阶段 0 表结构迁移 SQL 在 [infra/sql/alter-database/20260621_0200_create_agent_tables.sql](infra/sql/alter-database/20260621_0200_create_agent_tables.sql)。

Java 后端：

```powershell
cd backend-java
mvn spring-boot:run
```

文件上传默认使用本地 `uploads/rag`；生产上传到阿里 OSS 时配置以下环境变量，密钥不要写入仓库：

```powershell
$env:EVIDENCE_STORAGE_PROVIDER='oss'
$env:ALIYUN_OSS_BUCKET='<your-bucket>'
$env:ALIYUN_OSS_ENDPOINT='https://oss-cn-chengdu.aliyuncs.com'
$env:ALIYUN_OSS_ACCESS_KEY_ID='<your-access-key-id>'
$env:ALIYUN_OSS_ACCESS_KEY_SECRET='<your-access-key-secret>'
$env:ALIYUN_OSS_OBJECT_PREFIX='learning-evidence'
$env:ALIYUN_OSS_PUBLIC_BASE_URL='https://<your-bucket>.oss-cn-chengdu.aliyuncs.com'
```

React 前端：

```powershell
cd frontend-react
npm install
npm run dev
```

访问：`http://127.0.0.1:5178`

## 需要补全的环境变量

本项目不提交 `.env`、密钥或本地上传数据。使用者需要在系统环境变量、用户环境变量或本地未提交的 `.env` 中补全下列配置。

| 变量 | 是否必填 | 用途 | 示例或默认值 |
| --- | --- | --- | --- |
| `DASHSCOPE_API_KEY` | 使用百炼模型时必填 | 阿里云百炼 / DashScope 统一 API Key，当前用于百炼 Qwen-OCR 和 `text-embedding-v4` embedding | `<your-dashscope-api-key>` |
| `MINERU_TOKEN` | 使用 MinerU 云端能力时必填 | MinerU / OpenXLab API Token，供 MinerU 命令或第三方封装读取 | `<your-mineru-token>` |
| `MINERU_API_TOKEN` | 推荐同 `MINERU_TOKEN` | 兼容部分 MinerU 工具或 MCP 封装 | 与 `MINERU_TOKEN` 相同 |
| `MINERU_API_KEY` | 推荐同 `MINERU_TOKEN` | 兼容部分 MinerU 工具或 MCP 封装 | 与 `MINERU_TOKEN` 相同 |
| `MINERU_COMMAND` | 使用 MinerU 解析 PDF 时必填 | Python 通过该命令模板调用 MinerU，必须支持 `{input}` 和 `{output}` 占位 | `mineru -p {input} -o {output}` |
| `RAG_STORE_BACKEND` | 生产/联调推荐 | RAG 存储后端；未配置时 Python 单测和本地演示回退内存存储 | `pgvector` |
| `RAG_DATABASE_URL` | 使用 pgvector 时必填 | PostgreSQL/pgvector 连接串 | `postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic` |
| `RAG_DATABASE_SCHEMA` | 可选 | PostgreSQL schema 名，Python 启动时会确保该 schema 存在并设置 search_path | `learning_evidence` |
| `RAG_VECTOR_DIMENSIONS` | 可选 | pgvector 向量维度，需与数据库列一致 | `1024` |
| `RAG_EMBEDDING_MODEL` | 可选 | 百炼 embedding 模型名 | `text-embedding-v4` |
| `RAG_EMBEDDING_PROVIDER` | 可选 | embedding 提供方；生产默认 `dashscope`，单测或离线演示才显式设置 `hash` | `dashscope` |
| `RAG_EMBEDDING_BASE_URL` | 可选 | 百炼 OpenAI 兼容 embedding 接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `RAG_EMBEDDING_TIMEOUT_SECONDS` | 可选 | 单次 embedding 请求超时 | `30` |
| `SPRING_SERVLET_MULTIPART_MAX_FILE_SIZE` | 可选 | Java 单个 multipart 文件上限；长视频仍推荐走分片上传 | `512MB` |
| `SPRING_SERVLET_MULTIPART_MAX_REQUEST_SIZE` | 可选 | Java 单个 multipart 请求上限 | `512MB` |
| `SERVER_TOMCAT_MAX_SWALLOW_SIZE` | 可选 | Tomcat 处理超大请求体的吞吐上限 | `512MB` |
| `EVIDENCE_AI_INDEX_TIMEOUT_SECONDS` | 可选 | Java 等待 Python 索引结果的超时时间，长视频建议保留较大值 | `1800` |
| `EVIDENCE_UPLOAD_CHUNK_ROOT` | 可选 | Java 分片上传临时目录 | `uploads/chunks` |
| `BAILIAN_OCR_MODEL` | 可选 | 百炼 OCR 模型名 | `qwen3.5-ocr` |
| `BAILIAN_OCR_BASE_URL` | 可选 | 百炼 OpenAI 兼容接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `BAILIAN_OCR_ENABLED` | 可选 | 是否启用百炼 OCR；`auto` 表示存在 `DASHSCOPE_API_KEY` 时启用 | `auto` |
| `BAILIAN_OCR_TIMEOUT_SECONDS` | 可选 | 单次百炼 OCR 请求超时 | `60` |
| `BAILIAN_OCR_MAX_IMAGE_BYTES` | 可选 | 送入百炼 OCR 前允许的最大图片字节数 | `10485760` |
| `LIBREOFFICE_COMMAND` / `SOFFICE_COMMAND` | 可选 | DOC/PPT 转 PDF 或结构化格式时指定 LibreOffice 命令 | `soffice` |
| `OCR_LANG` | 可选 | 本地 `pytesseract` OCR 语言 | `chi_sim+eng` |
| `RAG_VIDEO_FFMPEG_TIMEOUT_SECONDS` | 可选 | FFmpeg 抽音频、分段和抽帧超时时间 | `1800` |
| `RAG_VIDEO_AUDIO_SEGMENT_SECONDS` | 可选 | 本地/私有长视频音频分段长度 | `300` |
| `RAG_VIDEO_AUDIO_OVERLAP_SECONDS` | 可选 | 音频分段前后重叠秒数，用于防止切断连续讲解 | `10` |
| `TAVILY_API_KEY` | Agent 联网搜索时必填 | Tavily Search API Key，用于 Agent 查询公司背景、技能趋势等外部信息 | `<your-tavily-api-key>` |
| `EVIDENCE_TAVILY_BASE_URL` | 可选 | Tavily API 基础地址 | `https://api.tavily.com` |
| `EVIDENCE_TAVILY_TIMEOUT_SECONDS` | 可选 | Tavily Search API 超时时间 | `15` |
| `VITE_API_PROXY_TARGET` | 前端代理自定义时可选 | Vite 开发代理指向 Java 后端 | `http://127.0.0.1:7080` |


## MinerU 接入

配置环境变量后，Python 文件索引会优先调用 MinerU：

```powershell
$env:MINERU_COMMAND='mineru -p {input} -o {output}'
```

命令需要把 Markdown 或 TXT 结果写入 `{output}` 目录。未配置或执行失败时，服务会使用本地解析降级，保证本地开发可运行。

## 百炼 OCR 接入

图片资料和 PDF 扫描页的 OCR 优先在 Python RAG 服务中调用阿里云百炼 Qwen-OCR。Java 不持有 Key，也不实现 OCR 逻辑；未配置或调用失败时自动降级为本地 `pytesseract`。本地降级依赖 `environment.yml` 中的 `tesseract` 可执行程序和 `OCR_LANG` 对应语言数据。

```powershell
$env:DASHSCOPE_API_KEY='<your-dashscope-api-key>'
$env:BAILIAN_OCR_MODEL='qwen3.5-ocr'
$env:BAILIAN_OCR_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
```

`BAILIAN_OCR_ENABLED=false` 可强制关闭远程 OCR。

## 验证命令

```powershell
conda activate learning-evidence-rag
$env:PYTHONPATH='ai-python'
python -B -m pytest ai-python/tests -q

cd backend-java
mvn test

cd frontend-react
npm run build
```

## Stitch 页面使用说明

前端基于 Chrome 中 Stitch 项目 `学迹智配管理后台` 生成页实现。已提取并固化：

- 左侧导航、顶部搜索栏、上传入口、工作台统计卡片。
- RAG 问答、多模态资料接入、JD 分析、视频切片、简历证据对齐模块。
- 主色 `#4F46E5`、辅色 `#0EA5E9`、浅色后台、约 8px 卡片圆角和 Inter 字体风格。

记录见 [docs/product/stitch-design-notes.md](docs/product/stitch-design-notes.md)。
