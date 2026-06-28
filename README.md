# 学迹智配 Agent：基于 RAG 的多模态学习证据库与岗位适配系统

英文标识：Multimodal RAG Agent Learning Evidence Platform

技术栈：React + Java Spring Boot + Python FastAPI + RAG。系统覆盖 RAG、视频证据、JD 适配、Agent 编排和记忆管理；MCP、自主长任务调度和多 Agent 协作不纳入项目能力范围。

## 项目速览

学迹智配 Agent 面向大学生和求职准备人群，把课程资料、项目文档、视频片段、岗位 JD、简历模板和历史偏好整理为可检索、可引用、可审批、可撤销的个人学习证据库。系统的核心原则是：React 只调用 Java；Java 负责登录、权限、业务状态、审计、审批、幂等和撤销；Python 负责 RAG 计算、Agent 编排、检索、重排、回答生成和 evidence 引用。

核心能力：

- 多模态资料入库：PDF、DOCX、PPTX、XLSX、Markdown、图片、字幕和原始视频。
- RAG 证据库：递归切块、摘要索引、BM25、PostgreSQL/pgvector、Multi-Query、RRF/RAG-Fusion 和 rerank。
- 视频证据：FFmpeg 抽音频、重叠 ASR 分段、PPT 翻页检测、关键帧 OCR、片段摘要和时间戳播放定位。
- 岗位适配：基于当前用户知识库对齐 JD 技能要求，输出已掌握、半掌握、缺口和学习计划。
- Codex 式 Agent 工作台：任务会话、计划、执行说明、思考摘要、工具调用、RAG evidence、审批卡片、变更快照和撤销窗口。
- 简历模板协作：模板抽取、字段补丁、Layout Guard、预览确认和导出。

默认入口：

| 服务 | 默认地址 | 说明 |
| --- | --- | --- |
| React 前端 | `http://127.0.0.1:5178` | Vite 开发服务 |
| Java 后端 | `http://127.0.0.1:7080` | 登录、权限、业务状态和统一 API |
| Python AI/RAG | `http://127.0.0.1:8090` | 内部 RAG、Agent、解析和检索服务 |
| PostgreSQL/pgvector | `127.0.0.1:5433` | RAG 与业务权威数据 |
| Redis | `127.0.0.1:6379` | Agent 短期运行态，可降级 |

默认本地管理员账号是 `admin / 123456`，仅用于本机开发和演示。

## 阅读导航

1. 先看“项目定位”“RAG 业务流程”和下方几张说明图，快速理解系统大致内容。
2. 再看“Agent 能力与边界”，确认哪些事情交给 Agent，哪些必须走 Java 审批和撤销。
3. 本地运行时再跳到“本地启动”“本地 Redis”和“需要补全的环境变量”。
4. API 细节见 [Agent 接口契约](docs/api/agent.md)、[RAG 接口契约](docs/api/rag.md) 和 [PostgreSQL/pgvector 建库说明](docs/database/postgresql-pgvector.md)。

## 项目定位

本项目面向大学生与求职准备人群，用于把个人学习资料、课程笔记、项目材料、视频片段和简历内容沉淀为可检索、可引用、可复用的个人学习证据库。RAG 是系统底座，Agent 编排、记忆管理和 JD 适配都建立在可追溯 evidence 之上：

- 文档识别：优先使用 MinerU，通过 `MINERU_COMMAND` 接入；未配置时走本地解析降级。
- 切块：使用递归切块，优先保留标题、段落、句子结构。
- 检索：Multi-Query + BM25 + PostgreSQL/pgvector 向量召回 + RRF/RAG-Fusion。
- 引用：回答返回证据片段、来源、章节和分数。

## RAG 业务流程

本项目的 RAG 不是前端直接调用 Python，也不是把 AI 逻辑写在 Java 里。业务边界是：React 只面向用户交互，Java Spring Boot 负责业务状态、资料记录、权限边界和统一 `Result<T>` 响应，Python FastAPI 负责文档识别、递归切块、索引、混合检索和证据引用。替换向量库、embedding 模型或增加重排序模型时，不需要破坏 Java 业务接口。

日志记录是横切能力：Java 统一接收并写入 `log_event` / `log_error`，RAG 使用 `domain=rag`，Agent 编排、审批和记忆链路也通过 Java 边界复用同一套 `domain/module/stage/action/errorCode/contextJson` 结构。

### RAG 闭环与视频证据流程图

```mermaid
flowchart LR
    A["资料上传<br/>文档、图片、字幕、转写文本、原始视频<br/>长视频自动分片上传"] --> B["文档 / 视频解析<br/>MinerU、重叠音频分段 ASR、PPT 翻页检测、OCR"]
    B --> C["结构化切块<br/>标题、章节、页面、字幕时间段、片段摘要"]
    C --> D["个人知识库 RAG<br/>Multi-Query + BM25 + 1024 维向量 + RAG-Fusion"]
    D --> E["JD 分析<br/>按当前用户知识库检索岗位技能证据"]
    E --> F["输出结果<br/>已掌握 / 半掌握 / 缺口 / 学习计划 / 证据引用"]
```

### 视频 RAG 能力

视频 RAG 支持两类入口：一是 `.srt`、`.vtt` 和带时间戳的 `.txt` 字幕/转写文本；二是 `.mp4/.mov/.webm/.mkv/.avi` 等原始视频文件。原始视频会先由 Java 上传到配置的对象存储；大于 20MB 的视频由前端自动切成 20MB 分片，Java 合并后保存，再把 `sourcePath` 交给 Python 的视频源索引接口，避免再次把整段视频通过 multipart 转发。Python 会基于本地路径或公开视频 URL 执行 FFmpeg 抽音频、百炼 ASR 生成字幕、候选帧采样、PPT 翻页检测、关键帧 OCR 和视频片段摘要，最终把字幕 evidence、画面 OCR evidence 与片段摘要 evidence 统一写入 RAG。命中结果会保留 `startTime/endTime/playbackUrl`，前端知识库证据卡片展示命中时间范围，并提供“从这里播放”的跳转入口。

为避免 2-3 小时长视频在分段处切断关键连续内容，本项目的本地/私有视频 ASR 默认按 5 分钟音频段处理，并在每段前后保留 10 秒重叠窗口。ASR 识别时使用带重叠的上下文，入库时保留与该段名义时间范围相交的字幕，再按全局时间轴合并和去重。这样可以减少边界处半句话、公式解释或代码步骤被切断的问题，同时避免重复 evidence。

典型回答形态是：“某课程视频 `01:23:10-01:25:42` 命中字幕证据，同时可结合对应 PPT 翻页画面的 OCR 证据说明 RAG-Fusion 流程，点击证据卡片的播放入口跳到视频复习页定位。” 如果 FFmpeg、百炼 ASR、PPT 翻页检测、OCR 或片段摘要任一环节报错，Python 会把 `video.audio.extract`、`video.asr`、`video.frame.extract`、`video.slide_detect`、`video.frame_ocr[n]`、`video.segment_summary`、`video.fallback` 等位置写入 `parseQuality.messages`；Java 在 `PARTIAL` 时写入 `log_error.contextJson.errorLocation`，并保留可追踪的视频元数据 evidence。补配环境后可在资料列表触发“重建索引”或“高精度补跑”，重新生成字幕、画面 OCR 和片段摘要 evidence。

### 完整视频 RAG 技术路线

下面是原始视频 RAG 的完整业务流程，覆盖上传保存、音频抽取、百炼 ASR、候选帧采样、PPT 翻页检测、关键帧 OCR、视频片段摘要、时间戳 evidence、错误位置日志和播放定位；视频上传、分片合并、索引和补跑走确定性 RAG 接口，不交给 Agent 自主处理。

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
    V2A -.报错.-> VERR["处理环节告警<br/>parseQuality.messages 带 video.* 位置"]
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
- 错误定位：Python 所有视频处理环节告警写入 `parseQuality.messages`，Java 读取后在 `PARTIAL` 时写入 `log_error.contextJson.errorLocation`。
- 补跑修复：资料列表提供重建索引和高精度补跑入口，Java 会从本地上传目录或阿里 OSS 重新读取原始文件，再调用 Python 重建同一 `documentId` 的 RAG 索引。

### 分域业务边界图

本节按领域拆分系统边界，避免把所有模块压进一张图里。共同原则是 React 只调用 Java API；Java 负责登录用户、权限、业务状态、对象存储、统一响应和日志；Python FastAPI 只作为内部 AI/RAG 计算服务被 Java 调用，不允许前端或 Agent 绕过 Java 直连 Python 内部接口、数据库或对象存储。

#### 访问、权限与存储边界

```mermaid
flowchart LR
    FE["React + Vite 前端<br/>页面、表单、预览和审批操作"] --> JAVA["Java API 边界<br/>Auth / Rag / PageData / Agent / Template"]
    JAVA --> AUTH["登录与权限<br/>app_user / auth_session / 当前用户"]
    JAVA --> BIZ["业务状态库<br/>learning_material / jd_* / agent_* / resume_template_*"]
    JAVA --> STORE["对象存储<br/>本地 uploads 或阿里 OSS"]
    JAVA --> LOG["统一日志<br/>log_event / log_error"]
```

#### RAG 与 JD 适配边界

```mermaid
flowchart LR
    FE["React<br/>资料上传、RAG 提问、JD 分析"] --> JAPI["Java RAG / PageData API<br/>资料状态、查询封装、用户归属"]
    JAPI -->|"文件、文本、sourcePath、查询请求"| PYRAG["Python RAG / JD 服务<br/>解析、切块、检索、重排、JD 适配"]
    PYRAG --> PG["PostgreSQL/pgvector<br/>rag_document / rag_chunk / BM25 / 向量"]
    PYRAG -->|"IndexResponse / RagQueryVO / JD 报告"| JAPI
    JAPI --> FE
```

#### Agent、记忆与工具边界

```mermaid
flowchart LR
    FE["Agent 工作台<br/>任务、计划审批、输出审批、撤销"] --> AGAPI["Java Agent API<br/>任务状态、审批、幂等、审计"]
    AGAPI --> AGDB["agent_task / agent_tool_call / agent_operation<br/>agent_memory_item / version / audit"]
    AGAPI -->|"内部 token"| PYAG["Python Agent / Memory<br/>pae_react_graph、记忆预取、候选提炼"]
    PYAG -->|"只能经 Tool Gateway"| AGAPI
    AGAPI -->|"复用业务能力"| DOMAIN["RAG、JD 和日志服务"]
```

### Agent 能力与边界

Agent 能力由 [Agent 接口契约](docs/api/agent.md)、`agent_*` 表结构、Java `AgentController` / `AgentMemoryController`、Python `pae_react_graph`、Java Tool Gateway、审批与撤销窗口共同组成。所有 Agent 工作台入口统一进入 Python `pae_react_graph` 任务路由器，再路由到 ReadOnly 子图或 Planning 子图；旧 `read_only_graph` / `planning_graph` 不再作为运行主图。支持的能力包括：任务创建和查询、只读 Tool Gateway、规划任务的计划审批、只读 evidence 对齐、能力缺口分析、输出审批、变更类 CRUD 审批、`web_search_probe` 联网参考工具、Agent 记忆候选提炼、记忆索引/检索。

MCP、`web_page_fetcher`、自主长任务调度、多 Agent 协作不纳入项目能力范围；资料重建索引、普通上传、分片上传或确定性 RAG 入库也不纳入 Agent 工具。Agent 编排继续遵守边界：React 只调用 Java；Java 负责登录用户、权限、业务状态、资料记录、统一响应和日志；Python 负责 Agent 图执行、工具观察整合、草稿生成、回答和 evidence 引用。Agent 不直接绕过 Java 调用 Python RAG 内部接口、数据库或对象存储。独立非 Agent 的普通资料上传、确定性视频索引、传统 `/jd-analysis` 页面继续走 RAG/JD 服务路径，不强行改成 Agent。

Agent 编排统一收敛到 Python `pae_react_graph`：任务路由器先选择 ReadOnly 子图或 Planning 子图；ReadOnly 子图覆盖只读问答、资料读取和检索覆盖诊断；Planning 子图采用 PAE（Plan-and-Execute）生成计划、完成标准和工具范围，再由 ReAct 式执行器按观察结果继续行动或修补。工具节点只负责适配 Java Tool Gateway。普通文件上传、分片上传和确定性入库不纳入 Agent 工具链，仍由 React 调用 Java 的现有资料接口完成。Agent 只在需要规划、观察、联网参考、evidence 对齐、JD/简历匹配、草稿生成、记忆提炼或人工审批的场景介入。工具注册面以 Java 业务能力为准；Agent 执行器只能通过 Java Tool Gateway 触发业务动作，由 Java 负责用户隔离、权限、状态机、日志、幂等和错误映射。

查询和只读工具不需要 Human-in-the-Loop，但必须由 Java 从登录 token 解析当前用户，并在 Tool Gateway 或业务服务层强制资源归属过滤：只能查询当前用户自己上传的数据，不能读取其他用户上传的资料。`explicitGrant(currentUserId, resourceId)` 是授权表扩展语义，未落表前非 owner 资源一律拒绝；Agent 不接收、也不自行构造跨用户资源过滤条件。Create/Update/Delete 以及任何业务状态变更必须进入 `human_crud_review` 做具体操作审批。计划确认只批准任务目标和工具路线，不等于批准任何写操作。自动写入 `log_event/log_error` 属于平台审计和故障观测副作用，不作为 Agent 可选工具，也不因工具审批而阻断；但日志必须由服务端补齐当前 `userId`、`traceId`，脱敏后写入，不能记录简历正文、资料正文或模型密钥。

```mermaid
flowchart TD
    START["Start / Java 创建 agent_task"] --> ROUTER["任务路由器<br/>选择 ReadOnly 子图或 Planning 子图"]
    ROUTER -->|"pure_read_query / general read"| R0["只读问答子图"]
    ROUTER -->|"planning_task / JD适配 / 复杂任务"| P0["规划执行子图"]

    subgraph READ["ReadOnly 子图：只读问答 / 覆盖诊断"]
      R0 --> RM0["记忆预取<br/>偏好、近期任务、上下文约束"]
      RM0 --> RP["轻量规划器<br/>确定只读工具路线与完成标准"]
      RP --> RE["只读执行器<br/>选择 RAG / 覆盖诊断 / 资料读取工具"]
      RE --> RT["Tool Adapter<br/>只允许 Java Read Gateway"]
      RT --> RR{"工具结果"}
      RR -->|"失败"| RREPAIR["修补节点<br/>重试 / 降级 / 汇报"]
      RREPAIR -->|"重试"| RT
      RREPAIR -->|"无法完成"| RA["只读验收器"]
      RR -->|"成功"| RA
      RA -->|"未完成"| RE
      RA -->|"通过"| RW["回答节点<br/>保留 evidence 引用"]
    end

    subgraph PLAN["Planning 子图：PAE + ReAct"]
      P0 --> PL["规划器 Planner<br/>先生成 PAE 计划、完成标准、工具范围"]
      PL --> HPLAN{"是否需要 PLAN 审批"}
      HPLAN -->|"是"| PLAN_REVIEW["HITL: PLAN 审批"]
      HPLAN -->|"否"| RWD{"Planner 是否检测到需要修改简历"}
      PLAN_REVIEW -->|"批准"| RWD
      PLAN_REVIEW -->|"修改"| PL
      PLAN_REVIEW -->|"拒绝"| PAW["回答节点"]
      RWD -->|"是"| RWS["简历修改子图<br/>生成改写计划与待确认候选"]
      RWD -->|"否"| PM1["记忆预取<br/>按已批准计划和工具意图检索"]
      RWS --> PM1
      PM1 --> EXEC["执行器 Executor<br/>ReAct 行动选择"]
      EXEC --> TOOL["Tool Adapter<br/>Java Gateway / 后续 MCP 适配"]
      TOOL --> TR{"工具结果"}
      TR -->|"失败"| REPAIR["修补节点<br/>重试 / 重规划 / 无法完成"]
      REPAIR -->|"重试"| TOOL
      REPAIR -->|"重规划"| PL
      REPAIR -->|"无法完成"| ACCEPT["验收器"]
      TR -->|"成功"| ACCEPT
      ACCEPT -->|"计划未完成"| EXEC
      ACCEPT -->|"需补救"| REPAIR
      ACCEPT -->|"通过"| PAW["回答节点"]
    end

    RW --> MEM["长期记忆整理<br/>候选提炼、冲突检测、版本建议"]
    PAW --> MEM
    MEM --> PENDING["PENDING_REVIEW<br/>当前图只生成待确认候选"]
    PENDING --> REVIEW_NOTE["前端 / Java 记忆审批流程<br/>确认后再触发保存"]
    REVIEW_NOTE --> SAVE["Java Mutation Gateway<br/>幂等、快照、撤销窗口"]
    PENDING --> END["End"]
    SAVE --> END
```

LangGraph 按 `docs/api/agent.md` 契约组织：`pae_react_graph` 统一纯只读和规划类路径。ReadOnly 路径为 `task_router -> memory_prefetch_before_planner -> planner -> memory_prefetch_after_planner -> executor -> tool_adapter -> repair/acceptance -> answer_writer -> post_answer_memory`；Planning 路径首轮为 `task_router -> planner -> plan_review`，用户批准计划后先进入 `resume_rewrite_decision`，需要改简历时执行 `resume_rewrite_planner -> resume_rewrite_generator -> resume_rewrite_acceptance`，再进入 `memory_prefetch_after_planner -> executor -> tool_adapter -> repair/acceptance -> answer_writer -> post_answer_memory`。`post_answer_memory` 当前只生成 `PENDING_REVIEW` 记忆候选，不在图内直接写入记忆；真正保存由前端/Java 的记忆确认或 CRUD 审批流程触发。`human_plan_review` 只确认复杂任务目标和工具路线，普通 RAG 查询、资料状态读取、evidence 读取和检索覆盖诊断不经过计划审批；`human_crud_review` 才批准具体保存类变更。撤销窗口通过 Java `POST /api/agent/operations/:id/undo` 暴露给前端，不作为 Python 可直接调用的 Tool Gateway 工具。

Planning 子图在 `PLAN` 审批通过后会先进入 `resume_rewrite_decision`。如果 Planner 在计划中标记 `resumeRewriteIntent=true`，或前端传入 `resume_rewrite_subgraph` / `resume_revision_save` / `resume_template_fill` 工具意图，统一图会进入简历修改子图；否则直接进入规划后记忆预取和常规 ReAct 执行链。简历修改子图只生成 `PENDING_REVIEW` 改写候选，不直接写 DOCX、不直接保存业务数据，后续仍由 `OUTPUT` 审批和可选 `CRUD` 审批决定是否保存。

### 简历修改子图

```mermaid
flowchart TD
    RS["进入条件<br/>PLAN 已批准 + Planner 检测到简历修改意图"] --> RD["resume_rewrite_decision<br/>读取计划、toolHints、目标和 JD/简历上下文"]
    RD -->|"不需要修改"| PM1["返回 Planning 主链<br/>memory_prefetch_after_planner"]
    RD -->|"需要修改"| RP["resume_rewrite_planner<br/>抽取 JD 要求、确定改写范围和护栏"]
    RP --> RG["resume_rewrite_generator<br/>生成摘要、技能、项目经历、补强建议候选"]
    RG --> RA{"resume_rewrite_acceptance<br/>候选是否可审批"}
    RA -->|"候选为空 / 失败"| AW["answer_writer<br/>汇报无法生成改写候选"]
    RA -->|"通过"| MERGE["并入 planning draft<br/>resumeRewrite.status=PENDING_REVIEW"]
    MERGE --> OUT["OUTPUT 审批<br/>用户确认或要求修改"]
    OUT -->|"批准且需要保存"| CRUD["CRUD 审批<br/>resume_revision_save"]
    OUT -->|"仅确认输出"| DONE["完成任务<br/>不写业务数据"]
    CRUD --> SAVE["Java Mutation Gateway<br/>幂等、快照、撤销窗口"]
```

固定图节点与 guardrail 不作为 Planner 可选工具，包括 `auth_context_resolver`、`scope_ownership_guard`、`intent_router`、Java/API 输入校验、受权限控制的上下文加载、`tool_router`、`java_read_tool_gateway`、`human_plan_review`、`human_crud_review`、`privacy_guard`、`citation_guard`、`undo_window_manager` 和 `audit_logging`。只读工具不需要人工审批，但必须通过 `java_read_tool_gateway` 强制当前用户归属过滤；`utc_time_provider` 为纯系统工具，不访问用户数据。网关只允许服务端计算 `ownerUserId == currentUserId`，`explicitGrant` 是预留能力，禁止 Agent 自行传入跨用户过滤条件。`rag_query_probe_non_persistent` 不写 `rag_query_history`，只允许写脱敏审计日志；保存查询历史必须按变更工具审批。`retrieval_coverage_probe` 只读取 Java 查询诊断里的 `expandedQueries`、候选数量、evidence 分布和覆盖率，不重新实现 Multi-Query、BM25、向量召回或 RAG-Fusion。

| 工具名 | 类型 | 何时调用 | 是否需要 HITL | Java 边界与权限约束 | 持久化副作用 |
| --- | --- | --- | --- | --- | --- |
| `material_status_reader` | 只读 | 用户查看资料解析、索引、失败原因或重试建议时 | 否 | 只走 `java_read_tool_gateway`，仅本人资料 | 无 |
| `material_evidence_reader` | 只读 | 需要列出资料标题、章节、片段、来源和分数时 | 否 | 只走 `java_read_tool_gateway`，仅本人 evidence | 无 |
| `material_preview_reader` | 只读 | 需要受限预览片段帮助判断是否补跑解析时 | 否 | 只走 `java_read_tool_gateway`，预览长度和字段由 Java 控制 | 无 |
| `rag_query_probe_non_persistent` | 只读 | 普通 RAG 问答或 JD/简历适配需要临时检索时 | 否 | 只走 Java 查询能力，强制 owner 范围 | 不写查询历史，仅写脱敏审计日志 |
| `retrieval_coverage_probe` | 只读 | evidence 覆盖不足，需要查看召回分布和覆盖率时 | 否 | 只读取 Java 返回的诊断摘要，不接触原始跨用户数据 | 无 |
| `evidence_quality_auditor` | 只读 | 输出前检查引用完整性、证据充分性和风险等级时 | 否 | 只走 `java_read_tool_gateway`，只处理已授权 evidence 和工具结果 | 无 |
| `resume_evidence_aligner` | 只读生成 | JD/简历适配时对齐 supported / weak / missing 证据 | 否 | 只走 `java_read_tool_gateway`，只读取当前用户简历、资料和授权 JD/资料 | 无，产出草稿 Observation |
| `gap_analyzer` | 只读生成 | 根据 evidence 缺口生成能力差距和学习建议时 | 否 | 只走 `java_read_tool_gateway`，只处理已授权 evidence 和 JD 要求 | 无，产出草稿 Observation |
| `utc_time_provider` | 只读 | Agent 需要获取当前 UTC 时间以判断技能、资料、证书时效性时 | 否 | 纯系统时间读取，不涉及用户数据 | 无 |
| `web_search_probe` | 只读 | 需要联网参考公司背景、技能趋势或外部学习资源时 | 否 | 只走 Java Tool Gateway，由 Java 调 Tavily，未配置密钥时返回可重试错误 | 无，外部结果不写入 RAG evidence |
| `agent_memory_retriever` | 只读 | Agent 任务启动或执行前需要读取当前用户可注入记忆时 | 否 | 只走 Java Tool Gateway，Java 负责用户归属和 ACTIVE 状态二次过滤 | 无，只返回 `memoryContext` |
| `agent_memory_candidate_proposer` | 只读生成 | 任务完成后需要提炼待确认记忆候选时 | 否 | Python 只做候选和冲突判断，Java 保存前做用户归属、去重和脱敏审计 | 无，候选默认不激活 |
| `jd_learning_plan_save` | 变更 | 用户确认保存学习路线或计划后 | 是，`human_crud_review` | 只走 Java Tool Gateway，校验当前用户计划归属 | 保存计划，可撤销 |
| `agent_task_cancel_request` | 变更 | 用户显式取消 Agent 任务或审批取消时 | 是，`human_crud_review` | 只走 Java Tool Gateway，只能取消本人任务 | 更新任务状态 |
| `agent_memory_candidate_save` | 变更 | 用户确认保存记忆候选或显式记住一条偏好时 | 是，`human_crud_review` | 只走 Java Tool Gateway，保存为当前用户记忆并触发 Python 索引 | 写 `agent_memory_item`、版本/审计和索引 |

暴露的变更工具包括 `jd_learning_plan_save`、`agent_task_cancel_request` 和 `agent_memory_candidate_save`。这些工具全部必须经过 `human_crud_review`、Java Tool Gateway 归属校验、幂等键校验、操作日志和撤销窗口；撤销操作由前端显式调用 Java `POST /api/agent/operations/:id/undo`，不是 Python Agent 可自行选择的工具。资料重建、普通上传、分片上传和确定性 RAG 入库继续走现有 RAG 接口。`jd_skill_extractor` 与 `resume_parser` 属于 JD/简历分析服务内部步骤，不作为普通 Tool。

变更操作的撤销窗口状态建议为：`PENDING_APPROVAL -> APPLIED_UNDOABLE -> UNDONE / UNDO_EXPIRED / FINALIZED / FAILED`。mutation 执行前必须生成 `beforeSnapshotRef`，执行后生成 `afterSnapshotRef/auditEventId/undoDeadline`。撤销只能恢复业务记录、草稿状态、计划记录或原索引引用；模型调用成本、已消耗的长视频处理资源不可撤销，只能在审批时明确 `costLevel` 和不可逆部分。

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
    AV1 -.报错.-> AVE["处理环节告警<br/>video.audio.extract / video.asr / video.frame_ocr[n] / video.segment_summary / video.fallback"]
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
    A18 -->|"PARTIAL 且有 messages"| A19E["Java 写入 log_error<br/>errorLocation 指向具体视频处理环节"]
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

### 索引流程说明：把学习资料变成可检索证据

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

### 查询流程说明：把问题变成带证据引用的回答

1. 用户在工作台或知识库页面输入问题。
2. 前端调用 Java `/api/rag/query`，Java 不做检索逻辑，只做统一接口和错误边界，然后调用 Python `/internal/rag/query`。
3. Python 先做 Multi-Query 扩展：保留原问题，再补充“关键证据”“学习资料/笔记”等查询变体；如果问题包含 JD、岗位、简历、项目等词，会补充更贴近岗位适配或简历证据的查询变体。
4. Python 按元数据过滤条件过滤候选切块。当前登录态由 Java `/api/auth/login` 处理，前端请求自动携带 Bearer Token；Java 将当前用户 ID 写入资料记录、Python 索引 metadata 和查询 `metadataFilter.userId`，默认管理员账号为 `admin / 123456`。
5. 每个查询变体同时走两路召回：BM25 负责关键词精确匹配，pgvector 负责向量相似度召回。
6. 多个查询变体、多个召回器的结果通过 RRF 做 RAG-Fusion 融合排序，避免单一路径漏召回。
7. 系统先保留 RAG-Fusion 候选证据，再通过百炼 `qwen3-rerank` 或本地确定性重排截取最终 evidence，并返回证据 ID、资料 ID、标题、片段、来源、章节、资料类型和分数。
8. 回答生成优先使用百炼 LLM 的 evidence 约束提示词，要求只基于证据回答并保留 `[evidenceId]` 引用；未配置 Key、调用失败或测试环境会降级为本地规则化摘要。

### 能力边界

- Python RAG 正式存储使用 PostgreSQL/pgvector，`rag_document` 保存资料摘要，`rag_chunk` 保存递归切块、元数据、BM25 词项统计和 `VECTOR(1024)` 向量，HNSW 索引使用 cosine 距离。
- 向量生成使用阿里云百炼 / DashScope `text-embedding-v4`，默认 1024 维，统一通过 `DASHSCOPE_API_KEY` 调用；单元测试可通过 `RAG_EMBEDDING_PROVIDER=hash` 使用离线确定性向量，生产环境不建议使用 hash provider。
- OCR 优先使用百炼 Qwen-OCR，未配置或失败时降级 `pytesseract`；Embedding 与 OCR 都收敛在 Python RAG 服务内，Java 不持有模型 Key。
- 视频 RAG 支持字幕 / ASR 转写文本，也支持原始视频经过 FFmpeg + 百炼 ASR、候选帧采样、PPT 翻页检测、关键帧 OCR 和视频片段摘要后入库；时间戳证据通过 `startTime/endTime/playbackUrl` 返回。
- 回答生成接入百炼 LLM 的 evidence 约束回答，未配置 Key 或测试环境会降级为规则化摘要；无证据时拒答并提示补充资料。
- Agent 支持 `pae_react_graph`、计划/输出/CRUD 审批、Java Tool Gateway、联网参考工具和记忆候选闭环；MCP、`web_page_fetcher`、自主长任务调度和多 Agent 协作不纳入项目能力范围。

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

### 运行前置条件

- JDK 17+、Maven 3.9+。
- Node.js 18+、npm。
- Conda 或 Miniforge，用于创建 `learning-evidence-rag` Python 环境。
- PostgreSQL + pgvector，默认连接 `127.0.0.1:5433/postgres`，schema 为 `learning_evidence`。
- Docker Desktop 可选，用于本地 Redis。
- 可选外部能力：DashScope/百炼、MinerU、LibreOffice、Tavily、阿里云 OSS。

Python AI 服务使用类似 Java 的配置格式，默认读取 `ai-python/config/application.yml`，可复制 `ai-python/config/application.local.example.yml` 为 `ai-python/config/application.local.yml` 做本机覆盖。`application.local.yml` 已被 `.gitignore` 忽略。

首次启动建议顺序：

1. 准备 PostgreSQL/pgvector，空库直接执行 [infra/sql/init.sql](infra/sql/init.sql)；已有库按 `infra/sql/alter-database/` 下的时间顺序执行增量脚本。
2. 需要实时 Agent 事件流时启动本地 Redis；未启动 Redis 时 Java 会走数据库轮询降级。
3. 先启动 Python AI/RAG 服务，再启动 Java 后端，最后启动 React 前端。

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

PostgreSQL/pgvector 建库和向量仓库创建语句见 [docs/database/postgresql-pgvector.md](docs/database/postgresql-pgvector.md)。完整初始化 SQL 在 [infra/sql/init.sql](infra/sql/init.sql)。已有数据库需要按顺序补跑增量迁移：生产 RAG 表 [20260616_0200_create_pgvector_rag_store.sql](infra/sql/alter-database/20260616_0200_create_pgvector_rag_store.sql)、Ragas 同库评估表 [20260621_0100_create_ragas_test_pgvector_store.sql](infra/sql/alter-database/20260621_0100_create_ragas_test_pgvector_store.sql)、Agent 权威状态表 [20260621_0200_create_agent_tables.sql](infra/sql/alter-database/20260621_0200_create_agent_tables.sql)、Agent 消息与运行事件表 [20260627_0100_create_agent_message_event_tables.sql](infra/sql/alter-database/20260627_0100_create_agent_message_event_tables.sql)。

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

## 本地 Redis

Agent 对话工作台使用 Redis 做短期运行态、SSE 低延迟事件流、纠偏摘要、取消标记和后续队列预留；PostgreSQL/H2 仍是任务、消息、审批、操作和运行事件的权威状态库。Redis 不可用时，Java 会降级到数据库事件轮询，任务查询和历史查看不应失效。

Docker Desktop 本地开发建议使用官方镜像：

```powershell
docker run -d --name learning-evidence-redis `
  -p 6379:6379 `
  -v C:\Users\WhenJayHe\docker-data\learning-evidence-redis:/data `
  redis:8-alpine redis-server --appendonly yes
```

本机直接运行的 Java、Python、Node/Vite 服务连接 `127.0.0.1:6379`。如果服务也运行在 Docker 容器内，容器内的 `127.0.0.1` 指向服务容器自身，不是宿主机 Redis；应使用同一 Docker network 下的 `learning-evidence-redis:6379`，或使用 `host.docker.internal:6379` 访问宿主机映射端口。

当前本地开发建议先不设置密码。后续启用密码时，官方 Redis 镜像不能只靠 `REDIS_PASSWORD` 环境变量，需要启动命令加 `redis-server --requirepass <密码>`，并同步配置 `SPRING_DATA_REDIS_PASSWORD` 和 `REDIS_URL=redis://:<密码>@<host>:6379/0`。

## 需要补全的环境变量

本项目不提交 `.env`、密钥或本地上传数据。使用者需要在系统环境变量、用户环境变量或本地未提交的配置文件中补全下列配置。敏感值不要写进 Git，也不要直接提交到 `application.yml`。推荐配置为 Windows 系统环境变量；如果用 PyCharm 启动 Python AI 服务，新增或修改系统环境变量后需要重启 PyCharm。

### 必填或常用敏感变量

| 变量 | 必填场景 | 用途 | 示例或默认值 |
| --- | --- | --- | --- |
| `DASHSCOPE_API_KEY` | 真实 RAG 联调必填 | 百炼 embedding、rerank、LLM、OCR 和 ASR | `<your-dashscope-api-key>` |
| `MINERU_TOKEN` / `MINERU_API_TOKEN` / `MINERU_API_KEY` | 使用 MinerU 云端能力时必填 | MinerU 命令或第三方封装鉴权 | `<your-mineru-token>` |
| `TAVILY_API_KEY` | Agent 联网搜索时必填 | Tavily Search API Key | `<your-tavily-api-key>` |
| `ALIYUN_OSS_ACCESS_KEY_ID` / `ALIYUN_OSS_ACCESS_KEY_SECRET` | Java 存储切到 `oss` 时必填 | Java 上传原始资料到阿里云 OSS | `<your-access-key>` |
| `EVIDENCE_INTERNAL_LOG_TOKEN` | 需要保护内部日志上报接口时填写 | Java 内部日志接口鉴权 token | 自定义随机字符串 |
| `EVIDENCE_AGENT_INTERNAL_TOKEN` | 生产或多机部署建议显式填写 | Java/Python Agent 内部调用鉴权；本地未配置时自动使用 `.local/agent-internal-token` | 自定义随机字符串 |

本地开发如果继续使用 `local` 文件存储，只需要先确认 `DASHSCOPE_API_KEY` 已在系统环境变量中配置。数据库密码默认使用本地值 `123456`，只适合本机开发；部署或共享环境应改为环境变量。

Agent 工作台本地联调不需要分别为 Java 和 Python 手工设置内部令牌。两个服务在未读取到 `EVIDENCE_AGENT_INTERNAL_TOKEN` 时，会自动读取或创建仓库根目录 `.local/agent-internal-token`；该目录已被 `.gitignore` 忽略，不要提交。生产、多机或容器部署时，建议显式配置 `EVIDENCE_AGENT_INTERNAL_TOKEN` 或用 `EVIDENCE_AGENT_INTERNAL_TOKEN_FILE` 指向统一密钥文件。

### 服务地址与数据源

| 变量 | 是否必填 | 用途 | 示例或默认值 |
| --- | --- | --- | --- |
| `AI_SERVICE_HOST` / `AI_SERVICE_PORT` | 可选 | Python FastAPI 监听地址和端口 | `127.0.0.1` / `8090` |
| `AI_SERVICE_RELOAD` | 可选 | Python 本地开发是否启用热重载 | `true` |
| `SPRING_DATASOURCE_URL` | 使用本机默认 PostgreSQL 以外配置时必填 | Java 业务库连接串 | `jdbc:postgresql://127.0.0.1:5433/postgres?currentSchema=learning_evidence,public` |
| `SPRING_DATASOURCE_USERNAME` / `SPRING_DATASOURCE_PASSWORD` | 可选 | Java 数据库账号密码 | `postgres` / `123456` |
| `RAG_STORE_BACKEND` | 生产/联调推荐 | Python RAG 存储后端；离线演示可改为 `memory` | `pgvector` |
| `RAG_DATABASE_URL` | 使用 pgvector 时必填 | PostgreSQL/pgvector 连接串 | `postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic` |
| `RAG_DATABASE_SCHEMA` | 可选 | PostgreSQL schema 名，Python 启动时会确保该 schema 存在并设置 search_path | `learning_evidence` |
| `RAG_EVENT_CALLBACK_URL` / `RAG_ERROR_CALLBACK_URL` | 可选 | Python RAG 处理日志回写 Java 内部日志接口 | `http://127.0.0.1:7080/api/logs/internal/events` / `.../errors` |
| `VITE_API_PROXY_TARGET` | 前端代理自定义时可选 | Vite 开发代理指向 Java 后端 | `http://127.0.0.1:7080` |
| `EVIDENCE_AGENT_JAVA_BASE_URL` | Python 调 Java 内部 Agent API 时可选 | Java Agent API 基础地址 | `http://127.0.0.1:7080` |

### RAG、模型与解析能力

| 变量 | 是否必填 | 用途 | 示例或默认值 |
| --- | --- | --- | --- |
| `RAG_VECTOR_DIMENSIONS` | 可选 | pgvector 向量维度，需与数据库列一致 | `1024` |
| `RAG_EMBEDDING_MODEL` | 可选 | 百炼 embedding 模型名 | `text-embedding-v4` |
| `RAG_EMBEDDING_PROVIDER` | 可选 | embedding 提供方；生产默认 `dashscope`，单测或离线演示才显式设置 `hash` | `dashscope` |
| `RAG_EMBEDDING_BASE_URL` | 可选 | 百炼 OpenAI 兼容 embedding 接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `RAG_EMBEDDING_TIMEOUT_SECONDS` | 可选 | 单次 embedding 请求超时 | `30` |
| `RAG_RERANK_PROVIDER` / `RAG_RERANK_MODEL` | 可选 | 检索后重排提供方和模型 | `auto` / `qwen3-rerank` |
| `RAG_RERANK_BASE_URL` / `RAG_RERANK_TIMEOUT_SECONDS` | 可选 | rerank 接口地址和超时时间 | `https://dashscope.aliyuncs.com` / `30` |
| `RAG_ANSWER_PROVIDER` / `RAG_LLM_MODEL` | 可选 | 回答生成提供方和模型 | `auto` / `qwen-plus` |
| `RAG_LLM_BASE_URL` / `RAG_LLM_TIMEOUT_SECONDS` | 可选 | LLM OpenAI 兼容接口地址和超时时间 | `https://dashscope.aliyuncs.com/compatible-mode/v1` / `45` |
| `RAG_LLM_TEMPERATURE` | 可选 | RAG 回答生成温度 | `0.2` |
| `MINERU_COMMAND` | 使用 MinerU 解析 PDF 时必填 | Python 调用 MinerU 的命令模板，必须支持 `{input}` 和 `{output}` 占位 | `mineru -p {input} -o {output}` |
| `BAILIAN_OCR_MODEL` | 可选 | 百炼 OCR 模型名 | `qwen3.5-ocr` |
| `BAILIAN_OCR_BASE_URL` | 可选 | 百炼 OpenAI 兼容接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `BAILIAN_OCR_ENABLED` | 可选 | 是否启用百炼 OCR；`auto` 表示存在 `DASHSCOPE_API_KEY` 时启用 | `auto` |
| `BAILIAN_OCR_TIMEOUT_SECONDS` | 可选 | 单次百炼 OCR 请求超时 | `60` |
| `BAILIAN_OCR_MAX_IMAGE_BYTES` | 可选 | 送入百炼 OCR 前允许的最大图片字节数 | `10485760` |
| `BAILIAN_OCR_MAX_ATTEMPTS` / `BAILIAN_OCR_RETRY_DELAY_SECONDS` | 可选 | OCR 失败重试次数和间隔 | `3` / `2` |
| `LIBREOFFICE_COMMAND` / `SOFFICE_COMMAND` | 可选 | DOC/PPT 转 PDF 或结构化格式时指定 LibreOffice 命令 | `soffice` |
| `OCR_LANG` | 可选 | 本地 `pytesseract` OCR 语言 | `chi_sim+eng` |

### 视频、文件上传与存储

| 变量 | 是否必填 | 用途 | 示例或默认值 |
| --- | --- | --- | --- |
| `SPRING_SERVLET_MULTIPART_MAX_FILE_SIZE` | 可选 | Java 单个 multipart 文件上限；长视频仍推荐走分片上传 | `512MB` |
| `SPRING_SERVLET_MULTIPART_MAX_REQUEST_SIZE` | 可选 | Java 单个 multipart 请求上限 | `512MB` |
| `SERVER_TOMCAT_MAX_SWALLOW_SIZE` | 可选 | Tomcat 处理超大请求体的吞吐上限 | `512MB` |
| `EVIDENCE_AI_INDEX_TIMEOUT_SECONDS` | 可选 | Java 等待 Python 索引结果的超时时间，长视频建议保留较大值 | `1800` |
| `EVIDENCE_UPLOAD_CHUNK_ROOT` | 可选 | Java 分片上传临时目录 | `uploads/chunks` |
| `EVIDENCE_STORAGE_PROVIDER` | 可选 | Java 文件存储后端 | `local` |
| `EVIDENCE_UPLOAD_ROOT` | 可选 | Java 本地上传目录 | `uploads/rag` |
| `ALIYUN_OSS_BUCKET` / `ALIYUN_OSS_ENDPOINT` | OSS 场景必填 | 阿里云 OSS bucket 和 endpoint | `<your-bucket>` |
| `ALIYUN_OSS_OBJECT_PREFIX` / `ALIYUN_OSS_PUBLIC_BASE_URL` | OSS 场景可选 | OSS 对象前缀和公开访问基础地址 | `learning-evidence` |
| `FFMPEG_COMMAND` / `FFPROBE_COMMAND` | 可选 | 环境外运行且不在 PATH 时填写完整路径 | 空 |
| `RAG_ASR_PROVIDER` / `RAG_ASR_MODEL` | 可选 | 视频 ASR 提供方和同步识别模型 | `auto` / `qwen3-asr-flash` |
| `RAG_ASR_FILETRANS_MODEL` / `RAG_ASR_FILETRANS_ENABLED` | 可选 | 公开视频 URL 异步转写模型和开关 | `qwen3-asr-flash-filetrans` / `auto` |
| `RAG_ASR_TIMEOUT_SECONDS` / `RAG_ASR_MAX_AUDIO_BYTES` | 可选 | 单次 ASR 请求超时和音频字节上限 | `120` / `10485760` |
| `RAG_VIDEO_FFMPEG_TIMEOUT_SECONDS` | 可选 | FFmpeg 抽音频、分段和抽帧超时时间 | `1800` |
| `RAG_VIDEO_AUDIO_SEGMENT_SECONDS` | 可选 | 本地/私有长视频音频分段长度 | `300` |
| `RAG_VIDEO_AUDIO_OVERLAP_SECONDS` | 可选 | 音频分段前后重叠秒数，用于防止切断连续讲解 | `10` |
| `RAG_VIDEO_FRAME_SCAN_MODE` / `RAG_VIDEO_FRAME_SAMPLE_INTERVAL_SECONDS` | 可选 | 视频候选帧扫描策略和采样间隔 | `auto` / `5` |
| `RAG_VIDEO_FRAME_TARGET_CANDIDATES` / `RAG_VIDEO_FRAME_MAX_CANDIDATES` | 可选 | 关键帧候选目标数和最大候选数 | `360` / `720` |
| `RAG_VIDEO_PPT_FLIP_DIFF_THRESHOLD` | 可选 | PPT 翻页检测差异阈值 | `0.08` |
| `RAG_VIDEO_SEGMENT_SECONDS` / `RAG_VIDEO_SEGMENT_MAX_CUES` | 可选 | 视频片段摘要窗口和最大字幕条数 | `120` / `6` |

### Agent、Redis 与联网工具

| 变量 | 是否必填 | 用途 | 示例或默认值 |
| --- | --- | --- | --- |
| `SPRING_DATA_REDIS_HOST` / `SPRING_DATA_REDIS_PORT` | 可选 | Java Agent SSE/运行时层连接 Redis | `127.0.0.1` / `6379` |
| `SPRING_DATA_REDIS_PASSWORD` / `SPRING_DATA_REDIS_DATABASE` | Redis 启用密码或多库时填写 | Java Redis 鉴权和库编号 | 空 / `0` |
| `SPRING_DATA_REDIS_TIMEOUT` | 可选 | Java Redis 连接超时 | `2s` |
| `REDIS_URL` | 可选 | Python Agent 读取短期纠偏和取消标记 | `redis://127.0.0.1:6379/0` |
| `EVIDENCE_AGENT_INTERNAL_TOKEN` | 可选，生产建议配置 | Java 与 Python Agent 内部接口共享令牌；本地未配置时自动读取或创建仓库根目录 `.local/agent-internal-token` | 自定义随机字符串 |
| `EVIDENCE_AGENT_INTERNAL_TOKEN_FILE` | 可选 | 指定 Java/Python 共用的内部令牌文件路径，便于容器或多进程复用同一密钥文件 | `.local/agent-internal-token` |
| `EVIDENCE_AGENT_START_TIMEOUT_SECONDS` | 可选 | Java 启动 Python Agent 任务的等待超时 | `10` |
| `EVIDENCE_AGENT_REDIS_ENABLED` | 可选 | Java 是否启用 Redis 运行态；关闭后走数据库轮询降级 | `true` |
| `EVIDENCE_AGENT_REDIS_STREAM_TTL_HOURS` | 可选 | Redis Stream 保留时间 | `24` |
| `EVIDENCE_AGENT_REDIS_CONTEXT_TTL_MINUTES` | 可选 | Redis 纠偏、取消、短期上下文 TTL | `120` |
| `EVIDENCE_AGENT_SSE_POLL_INTERVAL_MILLIS` | 可选 | SSE 数据库轮询间隔 | `2000` |
| `TAVILY_API_KEY` | Agent 联网搜索时必填 | Tavily Search API Key，用于 Agent 查询公司背景、技能趋势等外部信息 | `<your-tavily-api-key>` |
| `EVIDENCE_TAVILY_BASE_URL` | 可选 | Tavily API 基础地址 | `https://api.tavily.com` |
| `EVIDENCE_TAVILY_TIMEOUT_SECONDS` | 可选 | Tavily Search API 超时时间 | `15` |

配置占位符格式与 Java 保持一致，例如：

```yaml
rag:
  database:
    url: ${RAG_DATABASE_URL:postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic}
dashscope:
  api-key: ${DASHSCOPE_API_KEY:}
```

上表覆盖本地启动、联调和常见排障需要的配置。更细的评估、召回阈值、视频画面去重、Ragas 评测和简历模板生成参数，以 [backend-java/src/main/resources/application.yml](backend-java/src/main/resources/application.yml)、[ai-python/config/application.yml](ai-python/config/application.yml) 和 [ai-python/config/application.local.example.yml](ai-python/config/application.local.example.yml) 中的默认值为准。

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
