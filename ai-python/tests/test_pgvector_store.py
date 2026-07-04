import pytest

from app.schemas.rag import DocumentBlock
from rag.indexes.pgvector_store import PgVectorRagStore, normalize_table_prefix, quote_identifier
from rag.loaders.parse_quality import QualitySignals, evaluate_parse_quality
from rag.core.models import Chunk


class FakeChunker:
    def __init__(self, chunks):
        self.chunks = chunks

    def split_blocks(self, blocks, *, document_id, metadata):
        return self.chunks


class FakeCursor:
    def __init__(self, committed_count=None, rows=None):
        self.executed = []
        self.chunk_inserts = 0
        self.committed_count = committed_count
        self.rows = rows or []

    def execute(self, sql, params=None):
        sql_text = str(sql)
        self.executed.append((sql_text, params))
        if "INSERT INTO " in sql_text and "rag_chunk" in sql_text:
            self.chunk_inserts += 1

    def fetchone(self):
        return {"chunk_count": self.committed_count if self.committed_count is not None else self.chunk_inserts}

    def fetchall(self):
        return self.rows


class FakeContext:
    def __init__(self, value, on_enter=None):
        self.value = value
        self.on_enter = on_enter

    def __enter__(self):
        if self.on_enter:
            self.on_enter()
        return self.value

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.transaction_opened = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return FakeContext(self, lambda: setattr(self, "transaction_opened", True))

    def cursor(self):
        return FakeContext(self.cursor_obj)


def test_pgvector_index_blocks_rejects_empty_chunks(monkeypatch):
    store = PgVectorRagStore("postgresql://unused", ensure_schema=False)
    store.chunker = FakeChunker([])
    cleaned = []
    monkeypatch.setattr(store, "_delete_orphan_document_index", lambda document_id: cleaned.append(document_id))

    with pytest.raises(RuntimeError, match="递归切块结果为空"):
        store.index_blocks(
            document_id="doc-empty",
            title="空切块资料",
            document_type="markdown",
            source="unit-test",
            user_id="unit-user",
            visibility_scope="private",
            language="zh-CN",
            parser="unit-parser",
            blocks=[sample_block("doc-empty")],
            parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=8)),
            status="READY",
        )

    assert cleaned == ["doc-empty"]


def test_pgvector_index_blocks_writes_document_and_chunks_in_transaction(monkeypatch):
    store = PgVectorRagStore("postgresql://unused", ensure_schema=False)
    chunk = Chunk(
        chunk_id="doc-ok-0",
        document_id="doc-ok",
        text="RAG 事务写入需要保证 document 和 chunk 一致。",
        metadata={"chunkPosition": 0, "sectionName": "事务"},
    )
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    store.chunker = FakeChunker([chunk])
    monkeypatch.setattr(store, "_connect", lambda: connection)
    monkeypatch.setattr(store, "_json_adapter", lambda: (lambda value: value))
    monkeypatch.setattr("rag.indexes.pgvector_store.embed_text", lambda text, dimensions=None: [0.1] * 1024)

    result = store.index_blocks(
        document_id="doc-ok",
        title="事务资料",
        document_type="markdown",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser="unit-parser",
        blocks=[sample_block("doc-ok")],
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=24)),
        status="READY",
    )

    sql = "\n".join(item[0] for item in cursor.executed)
    assert result.chunkCount == 1
    assert connection.transaction_opened is True
    assert f"INSERT INTO {store.document_table}" in sql
    assert f"INSERT INTO {store.chunk_table}" in sql


def test_pgvector_store_uses_configured_test_table_prefix(monkeypatch):
    """确认评估可通过 Ragas_Test 表名前缀隔离 pgvector 表。"""
    monkeypatch.setenv("RAG_TABLE_PREFIX", "Ragas_Test_")

    store = PgVectorRagStore("postgresql://unused", ensure_schema=False)

    assert store.document_table_name == "Ragas_Test_rag_document"
    assert store.chunk_table_name == "Ragas_Test_rag_chunk"
    assert store.document_table == '"Ragas_Test_rag_document"'
    assert store.chunk_table == '"Ragas_Test_rag_chunk"'
    assert store.index_prefix == "idx_Ragas_Test_"
    assert normalize_table_prefix("Ragas_Test_") == "Ragas_Test_"
    assert quote_identifier("Ragas_Test_rag_document") == '"Ragas_Test_rag_document"'


def test_pgvector_store_rejects_invalid_table_prefix(monkeypatch):
    """确认动态表名前缀只允许安全的 PostgreSQL 标识符。"""
    monkeypatch.setenv("RAG_TABLE_PREFIX", "test-rag;")

    with pytest.raises(RuntimeError, match="RAG_TABLE_PREFIX"):
        PgVectorRagStore("postgresql://unused", ensure_schema=False)


def test_pgvector_index_blocks_cleans_index_when_committed_count_mismatches(monkeypatch):
    store = PgVectorRagStore("postgresql://unused", ensure_schema=False)
    chunk = Chunk(
        chunk_id="doc-mismatch-0",
        document_id="doc-mismatch",
        text="提交后还需要再次校验实际切块数。",
        metadata={"chunkPosition": 0, "sectionName": "事务"},
    )
    store.chunker = FakeChunker([chunk])
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    cleaned = []
    monkeypatch.setattr(store, "_connect", lambda: connection)
    monkeypatch.setattr(store, "_json_adapter", lambda: (lambda value: value))
    monkeypatch.setattr(store, "_count_document_chunks", lambda document_id: 0)
    monkeypatch.setattr(store, "_delete_document_index", lambda document_id: cleaned.append(document_id))
    monkeypatch.setattr("rag.indexes.pgvector_store.embed_text", lambda text, dimensions=None: [0.1] * 1024)

    with pytest.raises(RuntimeError, match="提交后计数不一致"):
        store.index_blocks(
            document_id="doc-mismatch",
            title="提交校验资料",
            document_type="markdown",
            source="unit-test",
            user_id="unit-user",
            visibility_scope="private",
            language="zh-CN",
            parser="unit-parser",
            blocks=[sample_block("doc-mismatch")],
            parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=20)),
            status="READY",
        )

    assert cleaned == ["doc-mismatch"]


def test_pgvector_promote_staged_index_is_idempotent(monkeypatch):
    """canonical 已由同一 staging/job 提升过时，重复 promote 直接成功。"""
    store = PgVectorRagStore("postgresql://unused", ensure_schema=False)

    def fake_count(document_id):
        return 3 if document_id in {"material-1", "material-1__job-job-1"} else 0

    monkeypatch.setattr(store, "_count_document_chunks", fake_count)
    monkeypatch.setattr(
        store,
        "_first_chunk_metadata",
        lambda document_id: {
            "sourceJobId": "job-1",
            "requestVersion": 2,
            "stagingDocumentId": "material-1__job-job-1",
        },
    )

    result = store.promote_staged_index(
        canonical_document_id="material-1",
        staging_document_id="material-1__job-job-1",
        job_id="job-1",
        request_version=2,
        expected_chunk_count=3,
    )

    assert result["alreadyPromoted"] is True
    assert result["canonicalChunkCount"] == 3


def test_pgvector_promote_rejects_lower_request_version(monkeypatch):
    """canonical 已是更高 requestVersion 时，旧 promote 不得覆盖。"""
    store = PgVectorRagStore("postgresql://unused", ensure_schema=False)

    def fake_count(document_id):
        return 2 if document_id in {"material-1", "material-1__job-job-old"} else 0

    monkeypatch.setattr(store, "_count_document_chunks", fake_count)
    monkeypatch.setattr(
        store,
        "_first_chunk_metadata",
        lambda document_id: {
            "sourceJobId": "job-new",
            "requestVersion": 3,
            "stagingDocumentId": "material-1__job-job-new",
        },
    )

    with pytest.raises(RuntimeError, match="旧版本 staging"):
        store.promote_staged_index(
            canonical_document_id="material-1",
            staging_document_id="material-1__job-job-old",
            job_id="job-old",
            request_version=2,
            expected_chunk_count=2,
        )


def test_pgvector_cleanup_staging_indexes_uses_retention_windows(monkeypatch):
    """过期 staging 清理只处理 job staging 文档，并区分成功和失败保留期。"""
    store = PgVectorRagStore("postgresql://unused", ensure_schema=False)
    cursor = FakeCursor(
        rows=[
            {"document_id": "material-1__job-job-1", "visibility_scope": "staging_promoted"},
            {"document_id": "material-2__job-job-2", "visibility_scope": "staging"},
        ]
    )
    connection = FakeConnection(cursor)
    deleted = []
    monkeypatch.setattr(store, "_connect", lambda: connection)
    monkeypatch.setattr(store, "_delete_document_index_with_cursor", lambda cursor, document_id: deleted.append(document_id))

    result = store.cleanup_staging_indexes(promoted_retention_hours=24, failed_retention_hours=168)

    select_sql, params = cursor.executed[0]
    assert "visibility_scope = 'staging_promoted'" in select_sql
    assert "visibility_scope = 'staging'" in select_sql
    assert params == ("%__job-%", 24, 168)
    assert deleted == ["material-1__job-job-1", "material-2__job-job-2"]
    assert result == {"promotedDeleted": 1, "failedDeleted": 1, "totalDeleted": 2}


def sample_block(document_id):
    return DocumentBlock(
        documentId=document_id,
        blockId=f"{document_id}-block",
        fileType="markdown",
        blockType="text",
        sectionTitle="事务",
        contentText="RAG 入库事务测试",
        parseEngine="unit-parser",
        sourceTitle="事务资料",
    )
