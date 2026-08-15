"""共享异步模型 HTTP 连接池与 RAG 回答降级测试。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import threading

import httpx
import pytest

from app.core.io_concurrency import (
    AsyncModelHttpClientPool,
    AsyncModelHttpConfig,
    LlmIoTimeoutError,
    async_model_http_config,
)
from app.schemas.rag import Evidence, QueryResponse
from app.schemas.rag_control import RagQueryPublicRequest
from app.services.rag_control_service import RagControlService
from rag.generation.bailian_llm import BailianChatClient, GeneratedAnswer


def pool_with_transport(
    handler,
    *,
    config: AsyncModelHttpConfig,
) -> tuple[AsyncModelHttpClientPool, list[httpx.AsyncClient]]:
    """创建使用离线 MockTransport 的池，并记录实际客户端创建次数。"""
    clients: list[httpx.AsyncClient] = []

    def factory(_: httpx.Limits, timeout: httpx.Timeout) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)
        clients.append(client)
        return client

    return AsyncModelHttpClientPool(config, client_factory=factory), clients


def sample_evidence() -> Evidence:
    """构造包含来源、章节、片段与分数的完整 evidence。"""
    return Evidence(
        evidenceId="evidence-async-1",
        documentId="document-async-1",
        documentTitle="异步网络笔记",
        title="异步网络笔记",
        snippet="AsyncClient 应复用连接池并保留 evidence 引用。",
        source="unit-test",
        sectionName="共享连接池",
        documentType="markdown",
        score=0.92,
        retrievalSource="rerank",
    )


def test_async_pool_configuration_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境配置必须限制连接数、keep-alive 和在途并发的异常大值。"""
    monkeypatch.setenv("ASYNC_MODEL_HTTP_MAX_CONNECTIONS", "999")
    monkeypatch.setenv("ASYNC_MODEL_HTTP_MAX_KEEPALIVE_CONNECTIONS", "999")
    monkeypatch.setenv("ASYNC_MODEL_HTTP_MAX_IN_FLIGHT", "999")
    monkeypatch.setenv("ASYNC_MODEL_HTTP_ACQUIRE_TIMEOUT_SECONDS", "0")

    config = async_model_http_config()

    assert config.max_connections == 128
    assert config.max_keepalive_connections == 128
    assert config.max_in_flight == 128
    assert config.acquire_timeout_seconds == 5.0


def test_async_pool_reuses_client_bounds_concurrency_and_keeps_loop_responsive() -> None:
    """并发请求复用一个客户端，在途数量有界且事件循环可继续调度。"""
    async def run() -> None:
        active = 0
        maximum_active = 0
        timeline: list[str] = []
        handler_threads: list[str] = []

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            handler_threads.append(threading.current_thread().name)
            timeline.append("request-start")
            await asyncio.sleep(0.03)
            timeline.append("request-finish")
            active -= 1
            return httpx.Response(200, json={"ok": True})

        pool, clients = pool_with_transport(
            handler,
            config=AsyncModelHttpConfig(
                max_connections=4,
                max_keepalive_connections=2,
                max_in_flight=2,
                acquire_timeout_seconds=0.2,
                default_timeout_seconds=0.2,
            ),
        )

        async def heartbeat() -> None:
            await asyncio.sleep(0.005)
            timeline.append("heartbeat")

        try:
            responses = await asyncio.gather(
                *(pool.request("GET", f"https://model.test/{index}") for index in range(4)),
                heartbeat(),
            )
            assert all(response.status_code == 200 for response in responses[:-1])
            assert len(clients) == 1
            assert maximum_active == 2
            assert timeline.index("heartbeat") < timeline.index("request-finish")
            assert all(not name.startswith("llm-io") for name in handler_threads)
        finally:
            client = clients[0]
            await pool.close()
            assert client.is_closed is True
            assert pool.is_closed is True

    asyncio.run(run())


def test_async_pool_bounds_queue_wait_and_request_timeout() -> None:
    """并发槽排队和网络请求都必须在各自预算内结束。"""
    async def run() -> None:
        release = asyncio.Event()
        started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/hold":
                started.set()
                await release.wait()
            else:
                await asyncio.sleep(0.1)
            return httpx.Response(200, json={"ok": True})

        pool, _ = pool_with_transport(
            handler,
            config=AsyncModelHttpConfig(
                max_connections=1,
                max_keepalive_connections=1,
                max_in_flight=1,
                acquire_timeout_seconds=0.01,
                default_timeout_seconds=0.2,
            ),
        )
        holding = asyncio.create_task(pool.request("GET", "https://model.test/hold"))
        await started.wait()
        try:
            with pytest.raises(LlmIoTimeoutError, match="并发槽"):
                await pool.request("GET", "https://model.test/queued")
        finally:
            release.set()
            await holding

        try:
            with pytest.raises(LlmIoTimeoutError, match="调用预算"):
                await pool.request("GET", "https://model.test/slow", timeout_seconds=0.01)
        finally:
            await pool.close()

    asyncio.run(run())


def test_async_bailian_rate_limit_keeps_local_fallback_and_evidence() -> None:
    """百炼限流仍返回本地证据回答，并保留可追踪引用和中文原因。"""
    async def run() -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"message": "rate limited"})

        pool, clients = pool_with_transport(
            handler,
            config=AsyncModelHttpConfig(max_in_flight=1, default_timeout_seconds=0.2),
        )
        client = BailianChatClient(
            api_key="unit-test-key",
            provider="dashscope",
            timeout_seconds=0.2,
            async_http_pool=pool,
        )
        try:
            result = await client.generate_async("为什么要复用连接池？", [sample_evidence()])
            assert result.provider == "local"
            assert result.model == "deterministic-grounded-answer"
            assert "触发限流" in (result.fallback_reason or "")
            assert "2 秒后重试" in (result.fallback_reason or "")
            assert "evidence-async-1" in result.answer
            assert "异步网络笔记" in result.answer
            assert len(clients) == 1
        finally:
            await pool.close()

    asyncio.run(run())


def test_rag_service_defers_final_answer_to_async_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """公开查询先在线程完成同步检索，再在事件循环异步生成并持久化最终回答。"""
    class Store:
        def __init__(self) -> None:
            self.defer_answer = False
            self.thread_name = ""
            self.metadata_filter: dict[str, object] = {}

        def query(self, request, progress_reporter=None, *, defer_answer: bool = False) -> QueryResponse:
            """记录同步检索所在线程，并返回等待异步生成的回答骨架。"""
            self.defer_answer = defer_answer
            self.thread_name = threading.current_thread().name
            self.metadata_filter = dict(request.metadataFilter or {})
            return QueryResponse(
                answer="",
                answerStatus="ANSWERED",
                confidence=0.92,
                supportingEvidenceIds=["evidence-async-1"],
                expandedQueries=[request.question],
                evidences=[sample_evidence()],
                diagnostics={"rerankProvider": "local"},
                progressEvents=list(progress_reporter.events),
            )

    class Repository:
        def __init__(self) -> None:
            self.history: list[dict[str, object]] = []

        @contextmanager
        def transaction(self):
            """提供查询历史写入所需的最小事务桩。"""
            yield self

        def insert_query_history(self, **kwargs) -> None:
            """记录最终持久化快照。"""
            self.history.append(kwargs)

    async def generate(question: str, evidences: list[Evidence]) -> GeneratedAnswer:
        """模拟真正异步的最终回答调用。"""
        assert question == "AsyncClient 如何复用？"
        assert [item.evidenceId for item in evidences] == ["evidence-async-1"]
        await asyncio.sleep(0)
        return GeneratedAnswer(answer="异步回答 [evidence-async-1]", provider="dashscope", model="qwen-plus")

    monkeypatch.setattr("app.services.rag_control_service.generate_grounded_answer_async", generate)
    monkeypatch.setenv("RAG_CONSOLE_PROGRESS_ENABLED", "false")
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = Store()
    repository = Repository()
    service = RagControlService(
        repository=repository,
        store=store,
        parser_router=object(),
        object_storage=object(),
        task_repository=object(),
    )

    response = asyncio.run(
        service.query_async(
            RagQueryPublicRequest(
                question="AsyncClient 如何复用？",
                metadataFilter={"userId": "attacker"},
            ),
            "user-42",
        )
    )

    assert store.defer_answer is True
    assert store.thread_name.startswith("asyncio")
    assert not store.thread_name.startswith("llm-io")
    assert store.metadata_filter["userId"] == "user-42"
    assert store.metadata_filter["visibilityScope"] == "private"
    assert response.answer == "异步回答 [evidence-async-1]"
    assert response.diagnostics["answerProvider"] == "dashscope"
    assert response.evidences[0].evidenceId == "evidence-async-1"
    assert repository.history[0]["answer"] == response.answer
    assert repository.history[0]["status"] == "COMPLETED"


def test_fastapi_lifespan_closes_shared_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务关闭必须释放 lifespan 启动的共享 AsyncClient。"""
    from app import main as main_module

    async def run() -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        pool, clients = pool_with_transport(
            handler,
            config=AsyncModelHttpConfig(max_in_flight=1, default_timeout_seconds=0.2),
        )
        monkeypatch.setattr(main_module, "async_model_http_pool", pool)
        async with main_module.app_lifespan(main_module.app):
            assert pool.is_started is True
            assert len(clients) == 1
            assert clients[0].is_closed is False
        assert pool.is_closed is True
        assert clients[0].is_closed is True

    asyncio.run(run())
