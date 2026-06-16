# Java 后端

Spring Boot 服务负责对外 API、资料记录和调用 Python RAG 服务。

## 启动

```powershell
cd backend-java
mvn spring-boot:run
```

默认端口：`8080`

## 分层

- `controller`：REST API，返回 `Result<T>`
- `service` / `service/Impl`：业务编排
- `mapper` + `resources/mapper`：MyBatis 持久化
- `client`：Java 到 Python FastAPI 的 HTTP 边界

## 主要接口

- `GET /api/rag/overview`
- `GET /api/rag/materials`
- `GET /api/rag/materials/{id}`
- `GET /api/rag/materials/{id}/evidences`
- `POST /api/rag/materials/text`
- `POST /api/rag/materials/upload`
- `POST /api/rag/query`

资料状态：`PENDING`、`PARSING`、`READY`、`PARTIAL`、`FAILED`、`REINDEXING`。Java 只保存业务状态、原始文件路径和调用 Python 的结果，不实现 MinerU、OCR、Embedding 或 RAG 检索逻辑。
