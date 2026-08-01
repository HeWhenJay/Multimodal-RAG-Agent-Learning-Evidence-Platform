"""资料原文受控预览与 RAG 提取回退测试。"""

from contextlib import contextmanager
from datetime import datetime, timezone

from app.core.result import BusinessError
from app.repositories.rag_control import MaterialRecord
from app.schemas.rag import Evidence
from app.services.rag_control_service import RagControlService, build_indexed_preview_content


def material(document_type: str = "pdf") -> MaterialRecord:
    """构造一条没有可直接暴露原始路径的资料记录。"""
    return MaterialRecord(
        id=12,
        title="Kafka 高可用课程",
        user_id="7",
        document_type=document_type,
        source="upload",
        status="READY",
        parser="mineru",
        document_summary="包含副本和故障转移知识点",
        chunk_count=2,
        original_filename="kafka.pdf",
        original_file_path="C:/private/kafka.pdf",
        storage_type="local",
        object_key=None,
        public_url=None,
        active_index_job_id=None,
        index_request_version=1,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def evidence(section: str, snippet: str) -> Evidence:
    """构造包含章节和页码定位信息的 RAG evidence。"""
    return Evidence(
        evidenceId="material-12-1",
        documentId="material-12",
        documentTitle="Kafka 高可用课程",
        title="Kafka 高可用课程",
        sectionName=section,
        sectionTitle=section,
        snippet=snippet,
        source="upload",
        documentType="pdf",
        pageIndex=3,
        score=1.0,
        retrievalSource="summary",
    )


class PreviewTransaction:
    """提供当前资料归属校验所需的最小事务。"""

    def find_material(self, material_id: int, user_id: str):
        return material() if material_id == 12 and user_id == "7" else None


class PreviewRepository:
    """复用内存资料事务，避免测试连接真实数据库。"""

    @contextmanager
    def transaction(self):
        yield PreviewTransaction()


class MissingOriginalStorage:
    """模拟原始文件已清理但 RAG 索引仍然可用的存储。"""

    def load_bytes(self, _material):
        raise BusinessError("原始文件不可读取")


class EvidenceStore:
    """返回已授权资料的索引 evidence。"""

    def list_evidences(self, document_id: str, limit: int):
        assert document_id == "material-12"
        assert limit == 1000
        return [evidence("副本机制", "每个分区由 Leader 和多个 Follower 副本组成。")]


def test_non_text_material_uses_indexed_preview_without_exposing_path() -> None:
    """PDF 原文跳转应返回 RAG 提取视图，而不是把本地路径交给浏览器。"""
    service = RagControlService(
        repository=PreviewRepository(),
        store=EvidenceStore(),
        object_storage=MissingOriginalStorage(),
        parser_router=object(),
        task_repository=object(),
    )

    preview = service.preview_material(12, None, "7")

    assert preview.contentType.startswith("text/markdown")
    assert "## 副本机制" in preview.content
    assert "> 页码 3" in preview.content
    assert "C:/private/kafka.pdf" not in preview.content


def test_indexed_preview_deduplicates_chunks_and_respects_length() -> None:
    """提取视图应去重重复 chunk，并限制单次响应体积。"""
    items = [evidence("核心", "相同片段"), evidence("核心", "相同片段")]

    content = build_indexed_preview_content("测试资料", items, maximum_length=80)

    assert content.count("相同片段") == 1
    assert len(content) <= 83
