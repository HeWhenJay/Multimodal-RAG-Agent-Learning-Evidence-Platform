# RAG 接口文档

更新日期：2026-06-17

## 变更摘要

本次补齐“多格式文档解析到 RAG 入库”接口契约，并接入登录用户隔离和 1024 维百炼 embedding。第一阶段只实现 RAG 闭环，不实现 Agent 编排、自主规划、长任务调度或工具调用。

边界约定：

- React 只调用 Java Spring Boot。
- Java 负责资料记录、文件上传、原始文件路径、解析状态、登录用户边界、统一 `Result<T>` 响应和调用 Python。
- Python FastAPI 负责多格式解析路由、MinerU/OCR 降级、`DocumentBlock` 统一模型、解析质量评分、递归切块、BM25、百炼 `text-embedding-v4` 1024 维向量索引、RRF/RAG-Fusion 和 evidence 引用。
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

### 上传并解析入库学习资料

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/materials/upload` |
| 请求类型 | `multipart/form-data` |
| 文件字段 | `file` |
| 可选字段 | `highPrecision`，布尔值，是否强制补跑高精度解析 |
| 支持格式 | `pdf/doc/docx/ppt/pptx/md/txt/srt/vtt/xls/xlsx/png/jpg/jpeg/webp` |
| 建议大小 | 单文件不超过 20MB |
| 响应 | `Result<LearningMaterialVO>` |

流程：

1. Java 保存原始文件到本地 `uploads/` 忽略目录。
2. Java 创建 `learning_material` 记录，初始状态 `PENDING`。
3. Java 调用 Python `/internal/rag/documents/index-file`，传入原始路径和高精度参数。
4. Python 按格式选择原生解析器，必要时补跑 PDF + MinerU/OCR。
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
- evidence 卡片展示页码、幻灯片、sheet、cell range、视频时间段、播放定位入口、解析器和检索来源。

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
