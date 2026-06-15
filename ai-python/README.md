# Python RAG 服务

## 启动

```powershell
python -m pip install -r ai-python/requirements.txt
$env:PYTHONPATH='ai-python'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

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
- 混合检索：BM25 + deterministic hash embedding
- 融合重排：RRF / RAG-Fusion

