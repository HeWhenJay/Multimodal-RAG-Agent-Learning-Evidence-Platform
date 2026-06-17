# RAG 接口文档

更新日期：2026-06-17

## 变更摘要

本次补齐“多格式文档解析到 RAG 入库”接口契约，并接入登录用户隔离和 1024 维百炼 embedding。第一阶段只实现 RAG 闭环，不实现 Agent 编排、自主规划、长任务调度或工具调用。

边界约定：

- React 只调用 Java Spring Boot。
- Java 负责资料记录、文件上传、阿里 OSS 对象存储、原始文件路径、解析状态、登录用户边界、统一 `Result<T>` 响应和调用 Python。
- Python FastAPI 负责多格式解析路由、原始视频处理、MinerU/OCR 降级、`DocumentBlock` 统一模型、解析质量评分、递归切块、BM25、百炼 `text-embedding-v4` 1024 维向量索引、RRF/RAG-Fusion、百炼 rerank、百炼 LLM 回答生成和 evidence 引用。
- 数据库初始化脚本位于 `infra/sql/init.sql`，增量迁移位于 `infra/sql/alter-database/`。

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
| 建议大小 | 单文件不超过 20MB |
| 响应 | `Result<LearningMaterialVO>` |

流程：

1. Java 按 `evidence.storage.provider` 保存原始文件。生产建议使用 `oss` 上传到阿里 OSS；本地测试默认使用 `local` 写入 `uploads/` 忽略目录。
2. Java 创建 `learning_material` 记录，初始状态 `PENDING`。
3. Java 调用 Python `/internal/rag/documents/index-file`，传入原始路径和高精度参数。
4. Python 按格式选择原生解析器；视频文件会先尝试 FFmpeg 抽音频、百炼 ASR 生成带时间戳字幕，再抽关键帧并用 OCR 识别画面文字；其他复杂版式必要时补跑 PDF + MinerU/OCR。
5. Python 返回 `READY/PARTIAL/FAILED`、切块数、解析器和摘要。
6. Java 回写资料状态并返回统一响应。

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
  "metadataFilter": {
    "documentType": "markdown",
    "visibilityScope": "private"
  }
}
```

Java 会覆盖或补充 `metadataFilter.userId` 为当前登录用户 ID，并默认限定 `visibilityScope=private`，避免前端传参越权。

响应新增 `diagnostics`，用于前端和调试确认检索链路：

```json
{
  "answerProvider": "dashscope",
  "answerModel": "qwen-plus",
  "rerankProvider": "dashscope",
  "rerankModel": "qwen3-rerank",
  "filteredChunkCount": 42
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
| 重建资料索引/补跑修复 | `POST /internal/rag/documents/index-file` | 60s | 不自动重试 | `documentId` |
| 资料 evidence | `GET /internal/rag/documents/{document_id}/evidences` | 30s | 不自动重试 | 无 |
| 检索问答 | `POST /internal/rag/query` | 30s | 不自动重试 | 无 |
| JD 适配分析 | `POST /internal/rag/jd-analysis` | 30s | 不自动重试 | 无 |
| 概览同步 | `GET /internal/rag/overview` | 5s | 不自动重试 | 无 |

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
    "needsSupplement": false
  }
}
```

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

视频字幕或 ASR 转写文本命中时，`startTime/endTime` 用于展示视频证据所在时间段，例如 `01:23:10-01:25:42`，`playbackUrl` 用于跳到视频复习页或真实视频 URL 的秒点。兼容字段：Java/前端仍可读取 `title`、`source`、`sectionName`、`documentType`，其值分别映射自 `documentTitle`、`sourcePath/source`、`sectionTitle` 和资料类型。

## 原始视频 RAG 策略

原始视频文件通过普通资料上传入口进入 Java，Java 先保存到 OSS 或本地，再把文件字节转发给 Python。Python 的视频处理链路如下：

```text
mp4/mov/webm/mkv/avi
-> FFmpeg 抽取 16kHz 单声道音频
-> 有公开视频 URL 时优先用百炼 qwen3-asr-flash-filetrans 生成带句级时间戳的 SRT 字幕
-> filetrans 不可用时降级 qwen3-asr-flash 同步转写
-> 字幕解析为带 startTime/endTime 的 DocumentBlock
-> FFmpeg 按时间间隔抽关键帧
-> 百炼 Qwen-OCR / pytesseract 识别关键帧文字
-> 画面 OCR 结果生成 evidenceChannel=frame_ocr 的 DocumentBlock
-> 字幕 evidence 与画面 evidence 统一进入 RAG
```

依赖和配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `FFMPEG_COMMAND` | PATH 中的 `ffmpeg` | 视频抽音频和抽关键帧 |
| `RAG_ASR_PROVIDER` | `auto` | `auto/local/dashscope`，生产有 Key 时走百炼 |
| `RAG_ASR_FILETRANS_ENABLED` | `auto` | 有公开视频 URL 时优先启用官方异步时间戳转写 |
| `RAG_ASR_FILETRANS_MODEL` | `qwen3-asr-flash-filetrans` | 百炼异步文件转写模型，返回句级时间戳 |
| `RAG_ASR_MODEL` | `qwen3-asr-flash` | filetrans 失败后的同步 ASR 降级模型 |
| `RAG_ASR_MAX_AUDIO_BYTES` | `10485760` | 同步 ASR 最大音频字节数 |
| `RAG_ASR_FILETRANS_MAX_POLLS` | `30` | 单次请求内等待异步转写结果的最大轮询次数 |
| `RAG_ASR_FILETRANS_POLL_INTERVAL_SECONDS` | `2` | 异步转写任务轮询间隔 |
| `RAG_VIDEO_FRAME_INTERVAL_SECONDS` | `30` | 关键帧抽取间隔 |
| `RAG_VIDEO_MAX_FRAMES` | `20` | 单个视频最多 OCR 的关键帧数 |

如果 FFmpeg、ASR 或 OCR 不可用，Python 会返回 `PARTIAL`，并至少保留视频元数据 evidence，方便前端展示失败原因和后续重建索引。同步 ASR 降级路径不保证模型一定返回真实时间戳，因此生产视频资料建议配置公开 OSS/CDN URL，让 filetrans 返回可验证的句级时间戳。

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

无 evidence 时直接拒答并提示上传资料；有 evidence 时，Prompt 要求回答只能基于 evidence，且关键结论保留 `[evidenceId]` 引用。测试环境默认走本地确定性回答和重排，避免消耗百炼额度。

## 多格式解析策略

| 格式 | 原生优先策略 | 补充策略 |
| --- | --- | --- |
| `pdf` | MinerU；失败后 PyMuPDF/pdfplumber/pypdf 可用方案 | OCR 可用时补充图片型页面 |
| `docx` | `python-docx` 提取标题、段落、表格、图片 | 低置信或高精度时 LibreOffice 转 PDF 后 MinerU/OCR |
| `doc` | LibreOffice headless 转 `docx` 和 `pdf` | 分别走 DOCX 原生解析与 PDF/MinerU |
| `pptx` | `python-pptx` 提取幻灯片标题、文本框、表格、图片、备注 | 低置信或高精度时渲染 PDF/图片后 MinerU/OCR |
| `ppt` | LibreOffice headless 转 `pptx` 和 `pdf` | 分别走 PPTX 原生解析与 PDF/MinerU |
| `md` | Markdown AST parser，保留标题、段落、列表、表格、代码块、图片链接 | AST 依赖不可用时退回结构化行解析 |
| `xlsx/xls` | `openpyxl/pandas` 解析 sheet、区域、坐标、公式、合并单元格 | 无文本区域时返回低置信 `PARTIAL/FAILED` |
| `png/jpg/jpeg/webp` | OCR | 预留图片摘要字段，不在 Java 中生成摘要 |
| `txt` | 编码探测后直接文本解析 | 解码失败时返回 `FAILED` |
| `srt/vtt` | 解析字幕 cue，保留开始和结束时间 | 作为 `mediaType=video`、`evidenceChannel=subtitle` 的时间戳证据入库 |
| `mp4/mov/m4v/webm/mkv/avi` | FFmpeg + 百炼 ASR + 关键帧 OCR | 失败时返回视频元数据 evidence，并标记 `PARTIAL` |

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
-> 内容清洗
-> 递归切块
-> 文档/章节/表格/图片摘要
-> BM25 索引
-> Embedding 向量索引
-> Evidence 元数据存储
```

切块规则：

- 文本块按标题、章节、页面、幻灯片、段落、句子递归切分。
- 表格、图片、代码块、公式和图表默认作为原子块保存，避免随意切碎。
- chunk metadata 保留 `blockId/blockType/pageIndex/slideIndex/sheetName/cellRange/startTime/endTime/sectionTitle/sourcePath/assetPath/parseEngine`。

## 前端影响

前端保持后台管理风格，只补充必要字段：

- 上传支持格式文案扩展，包含 `.srt/.vtt` 字幕和带时间戳的 `.txt` 转写文本。
- 资料列表显示 `PENDING/PARSING/READY/PARTIAL/FAILED/REINDEXING`。
- 上传资料卡片提供“重建索引”和“高精度补跑”入口；高精度补跑会调用 `/api/rag/materials/{id}/reindex?highPrecision=true`。
- evidence 卡片展示页码、幻灯片、sheet、cell range、视频时间段、播放定位入口、解析器和检索来源。

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
- HTTP 超时、401/403、5xx 或空响应都不会在 Java 中重试；Python 记录 warning 并按本地 OCR 降级。

### 状态和错误映射

| 情况 | Python 解析状态 | Java 行为 |
| --- | --- | --- |
| 百炼成功且获得文本 | `READY` 或由整体质量决定 | 保存 parser、摘要、chunk 数 |
| 百炼失败但本地 OCR 或原生解析可用 | `PARTIAL` | 保留已入库 evidence，前端显示“部分完成” |
| 百炼和本地 OCR 均无可索引文本 | `FAILED` | 返回资料记录，状态为解析失败 |
| Key 未配置 | 不视为接口错误 | 自动跳过百炼并使用本地降级链路 |
