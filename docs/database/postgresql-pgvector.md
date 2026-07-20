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
- `"Ragas_Test_rag_document"`，Ragas 评估专用资料表，使用生产同库 pgvector 环境
- `"Ragas_Test_rag_chunk"`，Ragas 评估专用切块表，使用生产同库 pgvector 环境
- RAG 元数据 GIN 索引
- pgvector HNSW 余弦索引

默认管理员账号：`admin / 123456`。密码以 PBKDF2 哈希种子写入，不保存明文。

在 PowerShell 中执行初始化：

```powershell
Get-Content infra\sql\init.sql -Encoding UTF8 |
  docker exec -i -e PGPASSWORD=123456 pgvector-postgres `
  psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1
```

## 纯 Python 非破坏性初始化

`infra/sql/init.sql` 是可审计的重建快照，直接执行会先 `DROP TABLE`，只适合明确
允许清空数据的临时环境。纯 Python 后端提供了安全 bootstrap 入口：它运行时读取
同一份 `init.sql`，跳过全部 `DROP`，并把 `CREATE TABLE/INDEX` 转换为
`IF NOT EXISTS`；默认管理员种子在账号已存在时使用 `DO NOTHING`，不会覆盖已有密码。

在仓库根目录执行 dry-run（不连接数据库）：

```powershell
$env:PYTHONPATH='ai-python'
conda run -n learning-evidence-rag python -B -m app.core.database_bootstrap --dry-run
```

确认输出后，对空 PostgreSQL/pgvector 数据库执行：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_DATABASE_URL='postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic'
conda run -n learning-evidence-rag python -B -m app.core.database_bootstrap
```

初始化事务失败会回滚；初始化完成后默认会调用 Python 的非破坏性增量迁移记录。
已有数据库不会执行删除操作，但 bootstrap 不是通用 schema 升级器：旧库仍应先按
`infra/sql/alter-database/` 的迁移记录升级，生产环境不要直接执行破坏性快照。

如需审计转换后的 SQL，可附加 `--print-sql`；如需跳过增量迁移，使用
`--skip-incremental-migrations`。

Python RAG 启动环境变量：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_STORE_BACKEND='pgvector'
$env:RAG_DATABASE_URL='postgresql://postgres:123456@127.0.0.1:5433/postgres?options=-csearch_path%3Dlearning_evidence%2Cpublic'
$env:RAG_DATABASE_SCHEMA='learning_evidence'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAG_EMBEDDING_MODEL='text-embedding-v4'
```

## Ragas 评估表

Ragas 效果评估必须使用 `RAG_DATABASE_URL` 指向的同一个 PostgreSQL/pgvector 数据库，不再派生单独测试库。评估数据通过带双引号的 `Ragas_Test` 前缀表隔离：

- `learning_evidence."Ragas_Test_rag_document"`
- `learning_evidence."Ragas_Test_rag_chunk"`

新增或升级现有数据库时执行迁移：

```powershell
Get-Content infra\sql\alter-database\20260621_0100_create_ragas_test_pgvector_store.sql -Encoding UTF8 |
  docker exec -i -e PGPASSWORD=123456 pgvector-postgres `
  psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1
```

## 128 维到 1024 维迁移

旧版本 `rag_chunk.embedding` 使用 `VECTOR(128)` 存储确定性 hash 向量。迁移到百炼 `text-embedding-v4` 后，向量列统一为 `VECTOR(1024)`。

已有 128 维向量不能无损转换为 1024 维真实 embedding。执行迁移脚本会清空旧 RAG 切块和向量仓库，并将已有学习资料状态标记为 `REINDEXING`，之后需要重新上传或重新索引资料。

```powershell
Get-Content infra\sql\alter-database\20260617_0100_migrate_embedding_1024.sql -Encoding UTF8 |
  docker exec -i -e PGPASSWORD=123456 pgvector-postgres `
  psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1
```
