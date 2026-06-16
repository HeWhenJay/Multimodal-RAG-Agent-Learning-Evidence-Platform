# RAG 接口文档

更新日期：2026-06-16

## 变更摘要

本次补齐“多格式文档解析到 RAG 入库”接口契约。第一阶段只实现 RAG 闭环，不实现 Agent 编排、自主规划、长任务调度或工具调用。

边界约定：

- React 只调用 Java Spring Boot。
- Java 负责资料记录、文件上传、原始文件路径、解析状态、统一 `Result<T>` 响应和调用 Python。
- Python FastAPI 负责多格式解析路由、MinerU/OCR 降级、`DocumentBlock` 统一模型、解析质量评分、递归切块、BM25、向量索引、RRF/RAG-Fusion 和 evidence 引用。
- 数据库初始化脚本位于 `infra/sql/init.sql`，增量迁移位于 `infra/sql/alter-database/`。

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
| 鉴权 | 第一阶段本地演示暂不强制，后续接登录态 |
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
| 支持格式 | `pdf/doc/docx/ppt/pptx/md/txt/xls/xlsx/png/jpg/jpeg/webp` |
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
| `user_id` | string | 是 | 第一阶段默认 `demo-user` |
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

`blockType` 取值：`heading/text/table/image/chart/formula/code/list`。

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
  "sectionTitle": "RAG 入库流程",
  "snippet": "原始文件 -> 解析路由 -> DocumentBlock -> 递归切块...",
  "sourcePath": "uploads/rag/20260616/1-系统设计笔记.pdf",
  "assetPath": null,
  "score": 0.0327,
  "retrievalSource": "fusion",
  "parseEngine": "mineru"
}
```

兼容字段：Java/前端仍可读取 `title`、`source`、`sectionName`、`documentType`，其值分别映射自 `documentTitle`、`sourcePath/source`、`sectionTitle` 和资料类型。

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
- chunk metadata 保留 `blockId/blockType/pageIndex/slideIndex/sheetName/cellRange/sectionTitle/sourcePath/assetPath/parseEngine`。

## 前端影响

前端保持后台管理风格，只补充必要字段：

- 上传支持格式文案扩展。
- 资料列表显示 `PENDING/PARSING/READY/PARTIAL/FAILED/REINDEXING`。
- evidence 卡片展示页码、幻灯片、sheet、cell range、解析器和检索来源。
