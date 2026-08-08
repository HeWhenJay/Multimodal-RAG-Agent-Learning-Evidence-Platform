"""SocialDataX 抖音 MCP 客户端测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from mcp.types import CallToolResult, TextContent

from app.schemas.rag import DocumentBlock, ParseQuality
from app.services.douyin_mcp_client import (
    DOUYIN_DETAIL_TOOL,
    DOUYIN_GET_TRANSCRIPT_JOB_TOOL,
    DOUYIN_SUBMIT_TRANSCRIPT_TOOL,
    DouyinTranscript,
    SocialDataXDouyinClient,
    annotate_douyin_transcript_result,
    build_mcp_tool_payload,
    parse_transcript_job_state,
    validate_douyin_mcp_endpoint,
)
from app.services.remote_video_import import RemoteVideoPermanentError
from rag.core.models import ParsedBlockDocument


def tool_result(data: dict | None = None, *, text: str = "", is_error: bool = False) -> CallToolResult:
    """构造同时覆盖结构化和文本返回的 MCP 工具结果。"""
    content = [TextContent(type="text", text=text)] if text else []
    return CallToolResult(content=content, structuredContent=data, isError=is_error)


def test_douyin_mcp_transcription_polls_job_and_builds_srt(monkeypatch) -> None:
    """提交未完成时按 job_id 轮询，并把时间分段转换为 SRT。"""

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def call_tool(self, name: str, arguments: dict, **_kwargs):
            self.calls.append((name, arguments))
            if name == DOUYIN_DETAIL_TOOL:
                return tool_result({"data": {"aweme_id": "741234567890", "desc": "RAG 面试知识讲解"}})
            if name == DOUYIN_SUBMIT_TRANSCRIPT_TOOL:
                return tool_result({"data": {"job_id": "job-1", "status": "processing"}})
            assert name == DOUYIN_GET_TRANSCRIPT_JOB_TOOL
            return tool_result(
                {
                    "data": {
                        "job_id": "job-1",
                        "status": "completed",
                        "segments": [
                            {"start_ms": 1250, "end_ms": 3250, "text": "先做多路召回。"},
                            {"start_ms": 3300, "end_ms": 5200, "text": "再使用 RRF 融合。"},
                        ],
                    }
                }
            )

    session = FakeSession()

    @asynccontextmanager
    async def fake_open_session():
        yield session

    client = SocialDataXDouyinClient(
        api_key="unit-test-key",
        poll_interval_seconds=0.001,
        max_wait_seconds=2,
    )
    monkeypatch.setattr(client, "_open_session", fake_open_session)
    polls: list[tuple[int, str]] = []

    transcript = client.transcribe_video(
        "https://www.douyin.com/video/741234567890",
        on_poll=lambda count, status: polls.append((count, status)),
    )

    assert transcript.title == "RAG 面试知识讲解"
    assert transcript.aweme_id == "741234567890"
    assert "00:00:01,250 --> 00:00:03,250" in transcript.text
    assert "再使用 RRF 融合。" in transcript.text
    assert polls == [(1, "processing")]
    assert [name for name, _arguments in session.calls] == [
        DOUYIN_DETAIL_TOOL,
        DOUYIN_SUBMIT_TRANSCRIPT_TOOL,
        DOUYIN_GET_TRANSCRIPT_JOB_TOOL,
    ]


def test_douyin_mcp_text_json_response_is_supported() -> None:
    """供应商只返回 TextContent JSON 时也能提取完成转写。"""
    result = tool_result(text='{"data":{"status":"success","speech_text":"混合检索需要 BM25 与向量召回。"}}')

    state = parse_transcript_job_state(
        build_mcp_tool_payload(result),
        source_url="https://www.douyin.com/video/741234567890",
    )

    assert state.completed is True
    assert state.transcript == "混合检索需要 BM25 与向量召回。"


def test_douyin_transcript_blocks_keep_video_evidence_metadata() -> None:
    """普通文本转写也必须保留视频来源、平台和 evidence 通道。"""
    block = DocumentBlock(
        documentId="material-1__job-job-1",
        blockId="block-1",
        fileType="txt",
        blockType="text",
        contentText="RAG-Fusion 使用倒数排名融合。",
        parseEngine="unit-parser",
        sourceTitle="旧标题",
        sourcePath=None,
    )
    parsed = ParsedBlockDocument(
        blocks=[block],
        parser="unit-parser",
        status="READY",
        parse_quality=ParseQuality(score=1.0, nativeTextChars=len(block.contentText)),
    )
    transcript = DouyinTranscript(
        title="抖音 RAG 课程",
        aweme_id="741234567890",
        source_url="https://www.douyin.com/video/741234567890",
        text=block.contentText,
    )

    annotated = annotate_douyin_transcript_result(parsed, transcript)

    assert annotated.parser == "socialdatax-douyin-mcp-speech-text"
    assert annotated.blocks[0].sourcePath == transcript.source_url
    assert annotated.blocks[0].metadata["sourcePlatform"] == "douyin"
    assert annotated.blocks[0].metadata["awemeId"] == "741234567890"
    assert annotated.blocks[0].metadata["evidenceChannel"] == "subtitle"


def test_douyin_mcp_requires_api_key_before_network_access() -> None:
    """未配置凭据时立即失败，不能发起匿名远程请求。"""
    client = SocialDataXDouyinClient(api_key="", enabled=True)

    with pytest.raises(RemoteVideoPermanentError, match="SOCIALDATAX_API_KEY"):
        client.transcribe_video("https://www.douyin.com/video/741234567890")


def test_douyin_mcp_endpoint_cannot_redirect_api_key_to_another_host() -> None:
    """endpoint 配置只能指向官方 HTTPS 地址。"""
    assert validate_douyin_mcp_endpoint("https://mcp.52choujiang.com/douyin/mcp") == (
        "https://mcp.52choujiang.com/douyin/mcp"
    )
    with pytest.raises(ValueError, match="官方 HTTPS 地址"):
        validate_douyin_mcp_endpoint("https://evil.example/douyin/mcp")


def test_douyin_mcp_auth_error_is_redacted(monkeypatch) -> None:
    """第三方鉴权正文不能进入公开任务错误，只返回稳定中文提示。"""

    class FakeSession:
        async def call_tool(self, _name: str, _arguments: dict, **_kwargs):
            return tool_result(text="401 unauthorized: secret upstream detail", is_error=True)

    @asynccontextmanager
    async def fake_open_session():
        yield FakeSession()

    client = SocialDataXDouyinClient(api_key="unit-test-key", max_wait_seconds=2)
    monkeypatch.setattr(client, "_open_session", fake_open_session)

    with pytest.raises(RemoteVideoPermanentError, match="鉴权失败") as caught:
        client.transcribe_video("https://www.douyin.com/video/741234567890")
    assert "secret upstream detail" not in str(caught.value)


def test_douyin_mcp_preserves_worker_lease_control_exception(monkeypatch) -> None:
    """worker 失租时客户端不能把控制异常改写为普通 MCP 重试错误。"""

    class IndexExecutionLostError(RuntimeError):
        pass

    class FakeSession:
        async def call_tool(self, name: str, _arguments: dict, **_kwargs):
            if name == DOUYIN_DETAIL_TOOL:
                return tool_result({"title": "测试视频"})
            return tool_result({"status": "completed", "transcript": "测试转写"})

    @asynccontextmanager
    async def fake_open_session():
        yield FakeSession()

    client = SocialDataXDouyinClient(api_key="unit-test-key", max_wait_seconds=2)
    monkeypatch.setattr(client, "_open_session", fake_open_session)

    with pytest.raises(IndexExecutionLostError):
        client.transcribe_video(
            "https://www.douyin.com/video/741234567890",
            cancel_check=lambda: (_ for _ in ()).throw(IndexExecutionLostError("租约失效")),
        )
