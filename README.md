# 学迹智配 Agent：基于 RAG 的多模态学习证据库与岗位适配系统

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
    A["资料上传<br/>文档、图片、字幕、转写文本"] --> B["文档 / 字幕 / 转写文本解析<br/>MinerU、OCR、结构化解析"]
    B --> C["结构化切块<br/>标题、章节、页面、字幕时间段"]
    C --> D["个人知识库 RAG<br/>Multi-Query + BM25 + 1024 维向量 + RAG-Fusion"]
    D --> E["JD 分析<br/>按当前用户知识库检索岗位技能证据"]
    E --> F["输出结果<br/>已掌握 / 半掌握 / 缺口 / 学习计划 / 证据引用"]
```

### 视频 RAG 第一阶段

当前视频 RAG 已支持两类入口：一是 `.srt`、`.vtt` 和带时间戳的 `.txt` 字幕/转写文本；二是 `.mp4/.mov/.webm/.mkv/.avi` 等原始视频文件。原始视频会先由 Java 上传到配置的对象存储，Python 再基于本次上传文件字节执行 FFmpeg 抽音频、百炼 ASR 生成字幕、关键帧抽取和 OCR，最终把字幕 evidence 与画面 OCR evidence 统一写入 RAG。命中结果会保留 `startTime/endTime/playbackUrl`，前端知识库证据卡片展示命中时间范围，并提供“从这里播放”的跳转入口。

典型回答形态是：“某课程视频 `01:23:10-01:25:42` 命中字幕证据，同时可结合对应 PPT/PDF 的 OCR 证据说明 RAG-Fusion 流程，点击证据卡片的播放入口跳到视频复习页定位。” 如果 FFmpeg、百炼 ASR 或 OCR 未配置，视频会进入 `PARTIAL`，并保留可追踪的视频元数据 evidence，方便后续补配环境后重建索引。

### 完整视频 RAG 技术路线

下面是原始视频 RAG 的完整业务流程。当前代码已覆盖上传保存、音频抽取、百炼 ASR、关键帧抽取、画面 OCR、时间戳 evidence 和播放定位；仍不在本阶段启动 Agent 自主处理。

```mermaid
flowchart TD
    V0["上传视频或课程资料包"] --> V1["保存视频文件和元数据<br/>课程名、来源、用户、可见范围"]
    V1 --> V2["FFmpeg 音频轨提取"]
    V2 --> V3["百炼语音识别 ASR<br/>输出带时间戳转写文本"]
    V1 --> V4["关键帧抽取<br/>按时间窗口或场景变化采样"]
    V4 --> V5["百炼 Qwen-OCR / 多模态 OCR<br/>识别板书、PPT、代码和图表文字"]
    V3 --> V6["字幕 / 转写文本解析<br/>生成 startTime、endTime"]
    V5 --> V7["画面文字块<br/>绑定 frameTime 和 sourcePath"]
    V6 --> V8["结构化切块<br/>保留时间戳、章节、来源"]
    V7 --> V8
    V8 --> V9["个人知识库 RAG<br/>Multi-Query + BM25 + 1024 维向量 + RRF"]
    V9 --> V10["回答与证据引用<br/>视频时间段、字幕片段、画面 OCR、相关 PPT/PDF"]
    V10 --> V11["播放定位<br/>videoUrl#t=秒 或视频复习页定位"]
```

技术选型：

- ASR：通过 `DASHSCOPE_API_KEY` 接入百炼语音识别模型，模型名保持配置化，输出优先转换为 `.srt/.vtt` 或带时间戳的 `.txt`。
- 画面 OCR：沿用 Python RAG 内的百炼 Qwen-OCR；未配置或失败时只对可降级场景使用本地 OCR，不在 Java 中实现识别逻辑。
- Embedding：统一使用百炼 `text-embedding-v4` 1024 维向量，pgvector 使用 HNSW + cosine。
- 检索：字幕、转写文本、PPT/PDF OCR 和文档切块进入同一 RAG 仓库，查询时通过 Multi-Query、BM25、向量召回和 RRF/RAG-Fusion 融合排序。
- 播放定位：evidence 保留 `startTime/endTime/playbackUrl`；有真实视频地址时使用 `videoUrl#t=秒`，无真实视频地址时跳到视频复习页展示定位信息。

### 整体业务流程图

```mermaid
flowchart TD
    U["用户：上传资料、粘贴笔记或输入问题"] --> FE["React 前端"]

    FE -->|"资料上传 / 文本索引"| JC["Java RAG 控制器"]
    JC --> JS["RAG 业务服务创建学习资料记录"]
    JS --> DB["MyBatis 持久层写入资料表<br/>PENDING -> PARSING"]
    JS --> PC["Python 服务调用客户端<br/>转发到 FastAPI"]
    JS -->|"关键状态 / RAG 错误"| LOG["通用日志服务<br/>domain=rag，可复用到 Agent"]
    PC -->|"Python 调用失败"| LOG

    PC --> PYI{"Python 解析入库入口"}
    PYI -->|"解析 / OCR / 索引异常内部上报"| LOG
    PYI -->|"文件"| ROUTE["多格式解析路由<br/>原生结构解析优先"]
    PYI -->|"文本"| TXT["转换为 DocumentBlock"]
    ROUTE --> BLOCK["统一 DocumentBlock<br/>页码/幻灯片/sheet/cell range"]
    BLOCK --> CH["递归切块<br/>标题 / 章节 / 页面 / 段落 / 句子"]
    TXT --> CH
    CH --> META["补充 evidence 元数据<br/>blockId、来源、解析器、置信度"]
    META --> SUM["摘要索引<br/>资料摘要 + 章节摘要"]
    SUM --> IDX["写入 PostgreSQL/pgvector<br/>BM25 词项统计 + 向量列"]
    IDX --> IR["返回 READY / PARTIAL / FAILED<br/>切块数、解析器、摘要"]
    IR --> JU["Java 更新资料记录"]
    JU --> FEI["前端展示解析状态、切块数和摘要"]

    FE -->|"RAG 提问"| JQ["Java 查询接口"]
    JQ -->|"查询开始 / 失败 / 无证据"| LOG
    JQ --> PQ["Python 查询入口"]
    PQ --> MQ["Multi-Query 扩展问题"]
    MQ --> FILTER["按元数据过滤条件筛选候选切块"]
    FILTER --> RET["BM25 + pgvector 并行召回"]
    RET --> FUSE["RRF / RAG-Fusion 融合排序"]
    FUSE --> EV["选择优先证据<br/>片段、来源、章节、分数"]
    EV --> ANS["生成带引用意识的回答摘要"]
    ANS --> JR["Java 封装统一响应对象"]
    JR --> UI["前端展示回答、扩展问题和证据引用"]
    LOG --> LDB["log_event / log_error<br/>traceId、stage、errorCode、contextJson"]
```

### 细分 RAG 流程图

这里把 RAG 拆成“索引流程”“查询流程”“检索流程”三张图。查询流程解决“用户问题如何进入 RAG 并形成响应”，检索流程解决“候选切块如何被召回、融合、排序并变成证据”。Java 只承载业务状态和统一响应，Python 承载 RAG 计算。

#### 索引流程图：资料到可检索证据

```mermaid
flowchart TD
    A0["用户提交学习资料<br/>文件或文本"] --> A1["React 提交表单<br/>标题、类型、来源、可见范围"]
    A1 --> A2["Java RAG 控制器<br/>校验空内容、文件大小、资料类型"]
    A2 --> A3["RAG 业务服务<br/>生成资料ID，状态写为 PENDING"]
    A3 --> A4["MyBatis 持久层<br/>保存资料记录"]
    A3 --> A5["Python 服务调用客户端<br/>按接口契约转发资料"]
    A5 --> A6{"输入是文件还是文本"}
    A6 -->|"文件"| A7["按格式选择解析器<br/>PDF / DOCX / PPTX / MD / XLSX / 图片 / TXT"]
    A7 --> A8{"原生解析质量是否足够"}
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
    A17 --> A18["Python 返回解析入库结果<br/>READY / PARTIAL / FAILED"]
    A18 --> A19["Java 回写资料状态<br/>完成、部分完成或失败"]
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
    B7 --> B8["进入检索流程<br/>执行召回、融合、排序"]
    B8 --> B9{"是否命中证据"}
    B9 -->|"否"| B10["空结果响应<br/>提示先上传资料或放宽过滤条件"]
    B9 -->|"是"| B11["回答生成策略选择"]
    B11 -->|"当前阶段"| B12["确定性回答摘要<br/>说明命中证据和推荐引用"]
    B11 -->|"后续扩展"| B13["LLM 提示词组装<br/>问题 + 证据 + 引用约束"]
    B13 --> B14["带引用答案<br/>只基于证据回答并保留出处"]
    B12 --> B15["Python 组装查询响应<br/>回答、扩展问题、证据、诊断信息"]
    B14 --> B15
    B10 --> B15
    B15 --> B16["Java 统一响应封装<br/>成功结果或错误映射"]
    B16 --> B17["React 展示结果<br/>回答、扩展问题、证据卡片、来源章节"]
```

#### 检索流程图：召回、融合、排序到证据

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
    C13 --> C14["按返回数量截取切块<br/>形成最终证据候选"]
    C14 --> C15["证据字段构造<br/>证据ID、资料ID、标题、片段、来源、章节、类型、分数"]
    C15 --> C16["片段压缩<br/>清理空白，超过 220 字符则截断"]
    C16 --> C17["返回证据列表和诊断信息<br/>扩展问题、过滤后切块数量"]
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
4. Python 按元数据过滤条件过滤候选切块。当前登录态由 Java `/api/auth/login` 处理，前端请求自动携带 Bearer Token；Java 将当前用户 ID 写入资料记录、Python 索引 metadata 和查询 `metadataFilter.userId`，默认管理员账号为 `admin@evidence.ai / 123456`。
5. 每个查询变体同时走两路召回：BM25 负责关键词精确匹配，pgvector 负责向量相似度召回。
6. 多个查询变体、多个召回器的结果通过 RRF 做 RAG-Fusion 融合排序，避免单一路径漏召回。
7. 系统按返回数量选择证据，并返回证据 ID、资料 ID、标题、片段、来源、章节、资料类型和融合分数。
8. 当前阶段生成的是确定性回答摘要：说明检索到几条证据、优先参考哪些资料和章节，并提醒正式输出保留证据引用。后续可以把这一步替换为真实 LLM 生成，但证据结构不需要改。

### 当前实现边界

- 当前 Python RAG 正式存储使用 PostgreSQL/pgvector，`rag_document` 保存资料摘要，`rag_chunk` 保存递归切块、元数据、BM25 词项统计和 `VECTOR(1024)` 向量，HNSW 索引使用 cosine 距离。
- 当前向量生成使用阿里云百炼 / DashScope `text-embedding-v4`，默认 1024 维，统一通过 `DASHSCOPE_API_KEY` 调用；单元测试可通过 `RAG_EMBEDDING_PROVIDER=hash` 使用离线确定性向量，生产环境不建议使用 hash provider。
- 当前 OCR 优先使用百炼 Qwen-OCR，未配置或失败时降级 `pytesseract`；Embedding 与 OCR 都收敛在 Python RAG 服务内，Java 不持有模型 Key。
- 当前视频 RAG 支持字幕 / ASR 转写文本，也支持原始视频经过 FFmpeg + 百炼 ASR + 关键帧 OCR 后入库；时间戳证据通过 `startTime/endTime/playbackUrl` 返回。
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

已创建过环境时，使用 `conda env update -f environment.yml --prune` 同步依赖即可。`ai-python/requirements.txt` 保留为 pip 兼容依赖清单，正式本地开发以 Conda 环境为准。

PostgreSQL/pgvector 建库和向量仓库创建语句见 [docs/database/postgresql-pgvector.md](docs/database/postgresql-pgvector.md)。完整初始化 SQL 在 [infra/sql/init.sql](infra/sql/init.sql)，增量迁移 SQL 在 [infra/sql/alter-database/20260616_0200_create_pgvector_rag_store.sql](infra/sql/alter-database/20260616_0200_create_pgvector_rag_store.sql)。

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
| `BAILIAN_OCR_MODEL` | 可选 | 百炼 OCR 模型名 | `qwen3.5-ocr` |
| `BAILIAN_OCR_BASE_URL` | 可选 | 百炼 OpenAI 兼容接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `BAILIAN_OCR_ENABLED` | 可选 | 是否启用百炼 OCR；`auto` 表示存在 `DASHSCOPE_API_KEY` 时启用 | `auto` |
| `BAILIAN_OCR_TIMEOUT_SECONDS` | 可选 | 单次百炼 OCR 请求超时 | `60` |
| `BAILIAN_OCR_MAX_IMAGE_BYTES` | 可选 | 送入百炼 OCR 前允许的最大图片字节数 | `10485760` |
| `LIBREOFFICE_COMMAND` / `SOFFICE_COMMAND` | 可选 | DOC/PPT 转 PDF 或结构化格式时指定 LibreOffice 命令 | `soffice` |
| `OCR_LANG` | 可选 | 本地 `pytesseract` OCR 语言 | `chi_sim+eng` |
| `VITE_API_PROXY_TARGET` | 前端代理自定义时可选 | Vite 开发代理指向 Java 后端 | `http://127.0.0.1:7080` |

## MinerU 接入

配置环境变量后，Python 文件索引会优先调用 MinerU：

```powershell
$env:MINERU_COMMAND='mineru -p {input} -o {output}'
```

命令需要把 Markdown 或 TXT 结果写入 `{output}` 目录。未配置或执行失败时，服务会使用本地解析降级，保证本地开发可运行。

## 百炼 OCR 接入

图片资料和 PDF 扫描页的 OCR 优先在 Python RAG 服务中调用阿里云百炼 Qwen-OCR。Java 不持有 Key，也不实现 OCR 逻辑；未配置或调用失败时自动降级为本地 `pytesseract`。

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
