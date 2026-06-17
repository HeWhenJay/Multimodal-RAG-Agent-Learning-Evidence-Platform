# 日志接口文档

更新日期：2026-06-16

## 变更摘要

新增通用日志接收 API，当前用于 RAG 业务状态和错误记录，后续 Agent 编排、工具调用、长任务调度也可以复用同一套结构。核心扩展字段是：

- `domain`：业务域，当前为 `rag`，后续可扩展为 `agent/system`。
- `module`：业务模块，如 `material/rag_query/evidence`。
- `stage`：业务阶段，如 `upload/index/retrieve/evidence/sync`。
- `action`：具体动作，如 `material_index_file_failed`。
- `errorCode`：可检索的错误码，如 `RAG_PYTHON_TIMEOUT`。
- `context`：脱敏后的 JSON 上下文，数据库以 JSON 字符串文本保存。

## 接口列表

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/logs/events` | 接收普通业务事件日志 |
| `POST` | `/api/logs/events/batch` | 批量接收普通业务事件日志 |
| `POST` | `/api/logs/errors` | 接收报错日志 |
| `POST` | `/api/logs/internal/errors` | 接收 Python 等内部服务错误日志 |
| `GET` | `/api/logs/events/recent` | 查看最近普通日志 |
| `GET` | `/api/logs/errors/recent` | 查看最近错误日志 |
| `GET` | `/api/logs/overview` | 查看日志概览 |

所有接口沿用 `Result<T>`：

```json
{
  "code": 1,
  "msg": null,
  "data": {}
}
```

## 普通日志接收

`POST /api/logs/events`

请求示例：

```json
{
  "source": "java",
  "domain": "rag",
  "level": "INFO",
  "module": "material",
  "stage": "index",
  "eventType": "business_state",
  "action": "material_index_file_result",
  "message": "文件资料索引完成",
  "materialId": 12,
  "documentId": "material-12",
  "parser": "mineru",
  "context": {
    "documentType": "pdf",
    "parseStatus": "READY",
    "chunkCount": 18
  }
}
```

响应：`Result<Long>`，返回日志 ID。

## 批量普通日志接收

`POST /api/logs/events/batch`

请求体：`LogEventCreateDTO[]`，默认最多写入 50 条。

响应：`Result<Integer>`，返回实际写入条数。

## 报错日志接收

`POST /api/logs/errors`

请求示例：

```json
{
  "source": "java",
  "domain": "rag",
  "severity": "ERROR",
  "module": "rag_query",
  "stage": "retrieve",
  "action": "rag_query_failed",
  "errorType": "PythonRagClientException",
  "errorCode": "RAG_PYTHON_TIMEOUT",
  "message": "RAG 查询失败",
  "materialId": null,
  "documentId": null,
  "context": {
    "questionLength": 32,
    "topK": 5,
    "pythonEndpoint": "/internal/rag/query",
    "elapsedMs": 30000
  }
}
```

响应：`Result<Long>`，返回错误日志 ID。

服务端会按 `source/domain/module/errorType/errorCode/message/topStackFrame` 生成 `fingerprint`。同一 fingerprint 再次上报时不新增记录，只更新 `last_seen_at` 和 `occurrence_count`。

## 内部错误上报

`POST /api/logs/internal/errors`

用途：Python RAG 服务主动上报解析、OCR、索引或检索内部错误。

Header：

```text
X-Internal-Log-Token: ${EVIDENCE_INTERNAL_LOG_TOKEN}
```

如果后端未配置 `evidence.logs.internal-token`，本地开发默认不强制校验该 Header。

## 查询接口

### 最近普通日志

`GET /api/logs/events/recent?limit=50`

响应：`Result<List<LogEventVO>>`

### 最近错误日志

`GET /api/logs/errors/recent?limit=50`

响应：`Result<List<LogErrorVO>>`

### 日志概览

`GET /api/logs/overview?days=7`

响应示例：

```json
{
  "eventCount": 120,
  "errorCount": 6,
  "openErrorCount": 2,
  "frontendErrorCount": 0,
  "javaErrorCount": 6,
  "pythonErrorCount": 0
}
```

## RAG 已接入记录点

| 位置 | 普通日志 | 错误日志 |
| --- | --- | --- |
| 文本索引 | `material_index_text_start/result` | `material_index_text_failed` |
| 文件上传 | `material_upload_saved` | `material_file_save_failed` |
| 文件索引 | `material_index_file_result` | `material_index_file_failed` |
| RAG 查询 | `rag_query_start/success/no_evidence` | `rag_query_failed` |
| evidence 查询 | 无 | `material_evidence_query_failed` |
| Java/Python 状态校验 | 无 | `RAG_INDEX_FAILED`、`RAG_DOCUMENT_ID_MISMATCH`、`RAG_READY_WITH_ZERO_CHUNK` |

## 脱敏规则

日志服务会对 `context` 做递归脱敏和截断。以下 key 会被替换为 `***`：

- `password`
- `token`
- `authorization`
- `cookie`
- `secret`
- `apiKey/api_key`
- `dashscope`
- `content`
- `question`
- `answer`
- `resume`
- `jd`

RAG 查询只记录 `questionLength/topK/metadataFilterKeys/evidenceCount` 等诊断信息，不记录问题全文、回答全文或资料正文。
