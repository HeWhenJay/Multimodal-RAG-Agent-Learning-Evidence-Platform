# 页面数据接口文档

更新日期：2026-07-21

## 变更摘要

Python FastAPI 接管原 Spring `PageDataController`。工作台和设置页面继续使用 `/api/page-data/*`，响应保持 Java `Result<T>` 信封，React 客户端无需修改。

```json
{
  "code": 1,
  "msg": null,
  "data": {}
}
```

`code=0` 表示认证、参数或业务错误，并保持 HTTP `200` 的既有业务信封语义。

## 数据边界

- PostgreSQL 连接串按 `PAGE_DATA_DATABASE_URL`、`RAG_DATABASE_URL`、`DATABASE_URL` 的顺序读取。
- schema 使用 `RAG_DATABASE_SCHEMA`，默认 `learning_evidence`。
- 工作台读取 `learning_material`、`log_event`、`log_error`；设置读取 `system_setting`。
- `dashboard` 的资料统计按当前认证用户隔离，系统错误统计保持全局口径，和 Java 实现一致。

## 接口列表

| 方法 | 路径 | 鉴权 | 用途 |
| --- | --- | --- | --- |
| `GET` | `/api/page-data/dashboard` | `Authorization: Bearer <token>` | 获取工作台聚合数据 |
| `GET` | `/api/page-data/settings` | 否 | 获取设置页展示项 |

## GET /api/page-data/dashboard

请求头：

```http
Authorization: Bearer base64url-token
```

查询参数：

| 参数 | 类型 | 默认值 | 规则 |
| --- | --- | --- | --- |
| `startDate` | `YYYY-MM-DD` | 按 `recentDays` 推导 | 仅允许最近 7 天内 |
| `endDate` | `YYYY-MM-DD` | 今天 | 仅允许最近 7 天内 |
| `recentDays` | 整数 | `7` | 截断到 `1..7` |
| `recentLimit` | 整数 | `5` | 截断到 `1..50` |

请求无 Token、Token 已过期或用户已停用时返回：

```json
{
  "code": 0,
  "msg": "登录状态已失效",
  "data": null
}
```

成功示例：

```json
{
  "code": 1,
  "msg": null,
  "data": {
    "materialCount": 12,
    "materialDelta7Days": 3,
    "evidenceCount": 186,
    "openErrorCount": 1,
    "errorCount30Days": 4,
    "recentTaskStartDate": "2026-07-14",
    "recentTaskEndDate": "2026-07-20",
    "recentTaskLimit": 5,
    "recentMaterials": []
  }
}
```

`recentMaterials` 保留资料页既有字段，包括 `latestProgress`、`progressEvents`、对象存储位置和解析状态。进度事件从 `log_event` 的 `domain=rag`、`event_type=rag_progress` 读取，去重后最多返回 30 条。

日期或数字查询参数非法时同样返回业务信封，例如：

```json
{
  "code": 0,
  "msg": "开始日期参数不合法",
  "data": null
}
```

资料统计只使用认证会话的用户 ID，客户端不能通过查询参数覆盖资料归属。

## GET /api/page-data/settings

返回 `system_setting` 全量展示项，排序规则为 `setting_group ASC, sort_order ASC, setting_key ASC`。

成功数据项：

```json
{
  "key": "rag.embedding.model",
  "group": "RAG",
  "label": "Embedding 模型",
  "value": "text-embedding-v4",
  "sortOrder": 10
}
```

## 测试替换边界

路由通过 `get_page_data_service` 和现有 `get_auth_service` 注入依赖。单元测试可以替换两者，不需要 PostgreSQL，也不会写入登录会话或业务资料；`/settings` 不读取认证会话。
