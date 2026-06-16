# Python RAG 服务

## 启动

```powershell
conda env create -f ai-python/environment.yml
conda activate learning-evidence-rag
$env:PYTHONPATH='ai-python'
$env:RAG_STORE_BACKEND='pgvector'
$env:RAG_DATABASE_URL='postgresql://learning_evidence_app:learning_evidence_app@127.0.0.1:5432/learning_evidence'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

已创建过环境时，使用 `conda env update -f ai-python/environment.yml --prune` 同步依赖。`requirements.txt` 只作为 pip 兼容依赖清单保留。

未配置 `RAG_DATABASE_URL` 时会退回内存后端，主要用于本地单元测试。正式运行使用 PostgreSQL/pgvector，建库和建表语句见 `docs/database/postgresql-pgvector.md` 与 `infra/sql/init.sql`。

## 接口

- `GET /health`
- `POST /internal/rag/documents/index-text`
- `POST /internal/rag/documents/index-file`
- `POST /internal/rag/query`
- `GET /internal/rag/overview`

## RAG 策略

- MinerU 文档识别适配入口：`MINERU_COMMAND`
- 递归切块：标题、段落、换行、句子、长度预算
- 摘要索引：文档摘要与章节摘要
- 混合检索：BM25 + PostgreSQL/pgvector 向量召回
- 融合重排：RRF / RAG-Fusion
- 持久化：`rag_document` 保存资料摘要，`rag_chunk` 保存切块、元数据、词频统计和 `VECTOR(128)` 向量
