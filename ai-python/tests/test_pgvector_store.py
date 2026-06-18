import pytest

from app.schemas.rag import DocumentBlock
from rag.indexes.pgvector_store import PgVectorRagStore
from rag.loaders.parse_quality import QualitySignals, evaluate_parse_quality
from rag.models import Chunk


class FakeChunker:
    def __init__(self, chunks):
        self.chunks = chunks

    def split_blocks(self, blocks, *, document_id, metadata):
        return self.chunks


class FakeCursor:
    def __init__(self, committed_count=None):
        self.executed = []
        self.chunk_inserts = 0
        self.committed_count = committed_count

    def execute(self, sql, params=None):
        sql_text = str(sql)
        self.executed.append((sql_text, params))
        if "INSERT INTO rag_chunk" in sql_text:
            self.chunk_inserts += 1

    def fetchone(self):
        return {"chunk_count": self.committed_count if self.committed_count is not None else self.chunk_inserts}


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
    assert "INSERT INTO rag_document" in sql
    assert "INSERT INTO rag_chunk" in sql


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
