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
- `GET /internal/rag/documents/{document_id}/evidences`
- `POST /internal/rag/query`
- `GET /internal/rag/overview`

## RAG 策略

- 多格式解析路由：`pdf/doc/docx/ppt/pptx/md/txt/xls/xlsx/png/jpg/jpeg/webp`
- MinerU 文档识别适配入口：`MINERU_COMMAND`
- 百炼 OCR 适配入口：`BAILIAN_OCR_API_KEY` 或 `DASHSCOPE_API_KEY`
- 原生结构解析优先：DOCX/PPTX/XLSX/Markdown/TXT 优先保留标题、段落、表格、图片、sheet 和 cell range
- 复杂版式补充解析：低置信或高精度模式时通过 LibreOffice 转 PDF 后补跑 MinerU/OCR
- 递归切块：标题、章节、页面、幻灯片、段落、句子、长度预算；表格、图片和代码块默认原子保存
- 摘要索引：文档摘要与章节摘要
- 混合检索：BM25 + PostgreSQL/pgvector 向量召回
- 融合重排：RRF / RAG-Fusion
- 持久化：`rag_document` 保存资料摘要，`rag_chunk` 保存切块、DocumentBlock/evidence 元数据、词频统计和 `VECTOR(128)` 向量

## 百炼 OCR 接入

图片文件和 PDF 扫描页优先使用百炼 Qwen-OCR；未配置 Key、调用失败或返回空文本时自动降级为本地 `pytesseract`。不要把 Key 写入配置文件或提交到 Git。

```powershell
$env:BAILIAN_OCR_API_KEY='<your-bailian-api-key>'
$env:BAILIAN_OCR_MODEL='qwen3.5-ocr'
$env:BAILIAN_OCR_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
```

可选项：

- `BAILIAN_OCR_ENABLED`：默认 `auto`，存在 Key 时启用；设置为 `false` 可强制禁用。
- `BAILIAN_OCR_TIMEOUT_SECONDS`：默认 `60`。
- `BAILIAN_OCR_MAX_IMAGE_BYTES`：默认 `10485760`。
