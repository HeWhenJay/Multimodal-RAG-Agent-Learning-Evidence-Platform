# PostgreSQL/pgvector 数据库初始化

本项目本地 Docker PostgreSQL/pgvector 连接约定：

- 容器名：`pgvector-postgres`
- 数据库：`postgres`
- Schema：`learning_evidence`
- 用户名：`postgres`
- 密码：`123456`
- 宿主机端口：`5433`
- Java JDBC：`jdbc:postgresql://127.0.0.1:5433/postgres?currentSchema=learning_evidence,public`
- Python URL：`postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic`

完整建表语句在 `infra/sql/init.sql`，包含：

- `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public`
- `CREATE SCHEMA IF NOT EXISTS learning_evidence AUTHORIZATION postgres`
- `app_user`
- `auth_session`
- `auth_login_record`
- `learning_material`
- `log_event`
- `log_error`
- `rag_document`
- `rag_chunk`
- RAG 元数据 GIN 索引
- pgvector HNSW 余弦索引

默认管理员账号：`admin / 123456`。密码以 PBKDF2 哈希种子写入，不保存明文。

在 PowerShell 中执行初始化：

```powershell
Get-Content infra\sql\init.sql -Encoding UTF8 |
  docker exec -i -e PGPASSWORD=123456 pgvector-postgres `
  psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1
```

Python RAG 启动环境变量：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_STORE_BACKEND='pgvector'
$env:RAG_DATABASE_URL='postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic'
$env:RAG_DATABASE_SCHEMA='learning_evidence'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAG_EMBEDDING_MODEL='text-embedding-v4'
```

## 128 维到 1024 维迁移

旧版本 `rag_chunk.embedding` 使用 `VECTOR(128)` 存储确定性 hash 向量。迁移到百炼 `text-embedding-v4` 后，向量列统一为 `VECTOR(1024)`。

已有 128 维向量不能无损转换为 1024 维真实 embedding。执行迁移脚本会清空旧 RAG 切块和向量仓库，并将已有学习资料状态标记为 `REINDEXING`，之后需要重新上传或重新索引资料。

```powershell
Get-Content infra\sql\alter-database\20260617_0100_migrate_embedding_1024.sql -Encoding UTF8 |
  docker exec -i -e PGPASSWORD=123456 pgvector-postgres `
  psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1
```
