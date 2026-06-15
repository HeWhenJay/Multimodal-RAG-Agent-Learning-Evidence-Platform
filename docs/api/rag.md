# RAG 接口文档

更新日期：2026-06-16

## 变更摘要

本项目第一阶段实现到 RAG 闭环，不实现 Agent 编排任务。前端只调用 Java Spring Boot，Java 负责业务状态、资料记录和权限边界，Python FastAPI 负责 MinerU 文档识别、递归切块、索引、混合检索和证据引用。

## Java 对外接口

### 获取 RAG 概览

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/api/rag/overview` |
| 鉴权 | 第一阶段本地演示暂不强制，后续接登录态 |
| 响应 | `Result<RagOverviewVO>` |

成功示例：

```json
{
  "code": 1,
  "msg": null,
  "data": {
    "materialCount": 3,
    "chunkCount": 24,
    "evidenceCount": 24,
    "lastIndexedTitle": "Java 并发编程笔记"
  }
}
```

### 上传并索引学习资料

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/rag/materials/upload` |
| 请求类型 | `multipart/form-data` |
| 文件字段 | `file` |
| 约束 | 支持 `md/txt/pdf/docx/pptx/html`，建议单文件不超过 20MB |
| 响应 | `Result<LearningMaterialVO>` |

Java 保存资料记录后，将文件转发到 Python `/internal/rag/documents/index-file`。Python 优先走 MinerU，未配置 MinerU 时使用本地解析降级。

失败示例：

```json
{
  "code": 0,
  "msg": "Python RAG 服务暂不可用，请稍后重试",
  "data": null
}
```

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
  "content": "## IOC\nSpring 容器负责对象创建与依赖注入..."
}
```

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
  "question": "如何解释 Spring Boot 自动配置？",
  "topK": 5,
  "metadataFilter": {
    "documentType": "markdown",
    "visibilityScope": "private"
  }
}
```

响应示例：

```json
{
  "code": 1,
  "msg": null,
  "data": {
    "answer": "根据已索引资料，Spring Boot 自动配置通过条件装配减少重复配置...",
    "expandedQueries": [
      "如何解释 Spring Boot 自动配置？",
      "Spring Boot 自动配置 证据",
      "Spring Boot 自动配置 学习资料"
    ],
    "evidences": [
      {
        "evidenceId": "doc-1-0",
        "documentId": "doc-1",
        "title": "Spring Boot 项目笔记",
        "snippet": "Spring Boot 自动配置通过条件注解...",
        "source": "manual",
        "sectionName": "IOC",
        "score": 0.0327
      }
    ]
  }
}
```

## Python 内部接口

### 健康检查

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | FastAPI 服务健康检查 |

### 索引文件

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/rag/documents/index-file` | 接收 Java 转发文件，使用 MinerU/降级解析后索引 |

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `file` | file | 原始文件 |
| `document_id` | string | Java 侧资料 ID |
| `title` | string | 资料标题 |
| `document_type` | string | `markdown/pdf/docx/pptx/html/text/video` |
| `source` | string | 来源 |
| `user_id` | string | 用户 ID，第一阶段默认 `demo-user` |
| `visibility_scope` | string | `private/public/team` |

### 索引文本

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/rag/documents/index-text` | 接收已提取文本并建立索引 |

### 检索问答

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/internal/rag/query` | Multi-Query + BM25/向量混合检索 + RRF 融合 |

### 概览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/internal/rag/overview` | 返回 Python 内存索引统计 |

## Java 调 Python 契约

| Java 动作 | Python Endpoint | 超时 | 重试 | 幂等键 |
| --- | --- | --- | --- | --- |
| 文本索引 | `POST /internal/rag/documents/index-text` | 20s | 不自动重试 | `documentId` |
| 文件索引 | `POST /internal/rag/documents/index-file` | 60s | 不自动重试 | `documentId` |
| 检索问答 | `POST /internal/rag/query` | 30s | 不自动重试 | 无 |
| 概览同步 | `GET /internal/rag/overview` | 5s | 不自动重试 | 无 |

错误映射：

| Python 状态 | Java Result |
| --- | --- |
| `400` | `Result.error("RAG 请求参数无效")` |
| `404` | `Result.error("未找到可检索资料")` |
| `5xx` | `Result.error("Python RAG 服务暂不可用，请稍后重试")` |
| 超时 | `Result.error("Python RAG 服务响应超时")` |

## 前端影响

前端新增：

- `frontend-react/src/api/rag.ts`
- `frontend-react/src/pages/Dashboard.tsx`
- `frontend-react/src/pages/KnowledgeBase.tsx`
- `frontend-react/src/pages/LearningMaterials.tsx`

用户态：

- 索引中：上传或文本索引后显示 `INDEXING`。
- 已完成：显示 `INDEXED`、chunk 数、更新时间。
- 检索为空：提示先上传或粘贴资料。
- Python 不可用：展示 Java 返回的错误，不直连 Python。

