# Java 后端

Spring Boot 服务负责对外 API、资料记录和调用 Python RAG 服务。

## 启动

```powershell
cd backend-java
mvn spring-boot:run
```

默认端口：`7080`

## 分层

- `controller`：REST API，返回 `Result<T>`
- `service` / `service/Impl`：业务编排
- `mapper` + `resources/mapper`：MyBatis 持久化
- `client`：Java 到 Python FastAPI 的 HTTP 边界

## 主要接口

- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/logout`
- `GET /api/rag/overview`
- `GET /api/rag/materials`
- `GET /api/rag/materials/{id}`
- `GET /api/rag/materials/{id}/evidences`
- `POST /api/rag/materials/text`
- `POST /api/rag/materials/upload`
- `POST /api/rag/query`
- `POST /api/page-data/jd-analysis/analyze`

默认初始化管理员：`admin@evidence.ai / 123456`。密码以 PBKDF2 哈希存储在 `app_user` 表中，登录 session 写入 `auth_session`，登录记录写入 `auth_login_record`。

RAG 和页面数据接口需要携带 `Authorization: Bearer <token>`。Java 会把当前登录用户 ID 写入 `learning_material.user_id`，并传递给 Python RAG 的 `userId` 和查询 `metadataFilter.userId`，确保资料列表、索引和检索只作用于当前用户的个人知识库。

资料状态：`PENDING`、`PARSING`、`READY`、`PARTIAL`、`FAILED`、`REINDEXING`。Java 只保存业务状态、原始文件路径和调用 Python 的结果，不实现 MinerU、OCR、Embedding 或 RAG 检索逻辑。
