import json
import os
import sys
import types
from pathlib import Path

import pytest

from evaluation import run_ragas_small_eval
from evaluation.ragas_eval_common import (
    RagasEvalSettings,
    EvaluationRunResult,
    build_ragas_input_row,
    build_ragas_model_adapter,
    calculate_document_hit,
    configure_current_rag_environment,
    ensure_ragas_eval_config,
    evaluate_boundary_case,
    filter_eval_cases,
    has_refusal_intent,
    has_valid_evidence_reference,
    load_json,
    load_jsonl,
    load_ragas_eval_settings,
    normalize_rag_profile,
    normalize_path,
    parse_ragas_metric_keys,
    ragas_result_to_rows,
    require_ragas_core_dependencies,
    snake_case_query_to_project_request,
    validate_ragas_test_table_prefix,
    write_ragas_scores_csv,
    _build_run_config,
    _call_ragas_evaluate,
)


def test_load_eval_cases_and_documents():
    """确认评估用例和文档清单可以按 UTF-8 正确读取。"""
    cases = load_jsonl(Path("docs/testing/ragas-small-eval-cases.jsonl"))
    documents = load_json(Path("docs/testing/ragas-small-eval-documents.json"))

    assert len(cases) == 12
    assert len(documents) == 10
    assert cases[0]["expected_document_ids"] == ["llm-ragas-d01"]
    assert documents[0]["documentId"] == "llm-ragas-d01"


def test_filter_eval_cases_supports_case_id_and_one_based_index():
    """确认评估用例可按 case_id 或 1 基序号单条筛选。"""
    cases = [{"case_id": "R01"}, {"case_id": "R02"}, {"case_id": "B01"}]

    assert filter_eval_cases(cases, case_id="R02") == [{"case_id": "R02"}]
    assert filter_eval_cases(cases, case_index=2) == [{"case_id": "R02"}]


def test_filter_eval_cases_rejects_invalid_selection():
    """确认单样本筛选参数错误时会给出中文报错。"""
    cases = [{"case_id": "R01"}]

    with pytest.raises(ValueError, match="只能二选一"):
        filter_eval_cases(cases, case_id="R01", case_index=1)
    with pytest.raises(ValueError, match="大于 0"):
        filter_eval_cases(cases, case_index=0)
    with pytest.raises(ValueError, match="未找到"):
        filter_eval_cases(cases, case_id="R99")
    with pytest.raises(ValueError, match="超出范围"):
        filter_eval_cases(cases, case_index=2)


def test_snake_case_query_to_project_request():
    """确认样本字段会转换为项目接口需要的 camelCase。"""
    case = {
        "question": "RAG-Fusion 如何排序？",
        "top_k": 5,
        "metadata_filter": {"userId": "ragas-small-eval", "visibilityScope": "private"},
    }

    payload = snake_case_query_to_project_request(case)

    assert payload == {
        "question": "RAG-Fusion 如何排序？",
        "topK": 5,
        "metadataFilter": {"userId": "ragas-small-eval", "visibilityScope": "private"},
    }


def test_normalize_path_uses_windows_style_comparison():
    """确认路径比较不会受斜杠方向和大小写影响。"""
    assert normalize_path("C:/Users/WhenJayHe/notes/study/demo.md") == "c:\\users\\whenjayhe\\notes\\study\\demo.md"


def test_normalize_rag_profile_allows_current_project_flow(monkeypatch):
    """确认 Ragas 评估只允许当前项目真实 RAG 全流程。"""
    monkeypatch.setenv("RAGAS_EVAL_RAG_PROFILE", "current")

    assert normalize_rag_profile(None) == "current"

    with pytest.raises(ValueError, match="只允许 current"):
        normalize_rag_profile("deterministic")


def test_normalize_rag_profile_defaults_to_current_project_flow(monkeypatch):
    """确认评估默认使用生产同款 RAG 全流程。"""
    monkeypatch.delenv("RAGAS_EVAL_RAG_PROFILE", raising=False)

    assert normalize_rag_profile(None) == "current"


def test_validate_ragas_test_table_prefix_requires_ragas_test_prefix():
    """确认评估表前缀必须带 Ragas_Test，避免误写生产表。"""
    assert validate_ragas_test_table_prefix("Ragas_Test_") == "Ragas_Test_"

    with pytest.raises(RuntimeError, match="必须以 Ragas_Test 开头"):
        validate_ragas_test_table_prefix("test_")


def test_configure_current_rag_environment_reuses_project_components(monkeypatch):
    """确认 current 档位复用项目 RAG 配置，只替换 Ragas_Test 表前缀。"""
    database_url = "postgresql://postgres:123456@127.0.0.1:5433/postgres"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("RAG_DATABASE_URL", database_url)
    monkeypatch.setenv("RAG_STORE_BACKEND", "pgvector")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "text-embedding-custom")
    monkeypatch.setenv("RAG_ANSWER_PROVIDER", "auto")
    monkeypatch.setenv("RAG_LLM_MODEL", "qwen-custom")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "auto")
    monkeypatch.setenv("RAG_RERANK_MODEL", "rerank-custom")
    monkeypatch.setattr("evaluation.ragas_eval_common.load_current_project_rag_config", lambda: None)

    configure_current_rag_environment()

    assert os.environ["RAG_DATABASE_URL"] == database_url
    assert os.environ["RAG_STORE_BACKEND"] == "pgvector"
    assert os.environ["RAG_TABLE_PREFIX"] == "Ragas_Test_"
    assert os.environ["RAG_EMBEDDING_PROVIDER"] == "dashscope"
    assert os.environ["RAG_EMBEDDING_MODEL"] == "text-embedding-custom"
    assert os.environ["RAG_ANSWER_PROVIDER"] == "auto"
    assert os.environ["RAG_LLM_MODEL"] == "qwen-custom"
    assert os.environ["RAG_RERANK_PROVIDER"] == "auto"
    assert os.environ["RAG_RERANK_MODEL"] == "rerank-custom"


def test_configure_current_rag_environment_rejects_non_pgvector_backend(monkeypatch):
    """确认评估不会在 memory/hash 这类非生产存储配置下继续运行。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql://postgres:123456@127.0.0.1:5433/postgres")
    monkeypatch.setenv("RAG_STORE_BACKEND", "memory")
    monkeypatch.setattr("evaluation.ragas_eval_common.load_current_project_rag_config", lambda: None)

    with pytest.raises(RuntimeError, match="RAG_STORE_BACKEND 需要为 pgvector"):
        configure_current_rag_environment()


def test_calculate_document_hit_top1_and_top3():
    """确认文档级 top1/top3 命中计算符合人工核验口径。"""
    assert calculate_document_hit(["llm-ragas-d02"], ["llm-ragas-d02", "llm-ragas-d01"])["top1_hit"]
    top3 = calculate_document_hit(["llm-ragas-d03"], ["llm-ragas-d01", "llm-ragas-d02", "llm-ragas-d03"])
    assert not top3["top1_hit"]
    assert top3["top3_hit"]
    assert top3["hit_rank"] == 3
    assert not calculate_document_hit(["llm-ragas-d04"], ["llm-ragas-d01", "llm-ragas-d02", "llm-ragas-d03"])["top3_hit"]


def test_has_valid_evidence_reference_requires_project_reference_fields():
    """确认引用结构必须能追踪到 evidenceId 和来源字段。"""
    evidence = {
        "evidenceId": "chunk-1",
        "title": "RAG 笔记",
        "sectionName": "检索评估",
        "source": "ragas-small-eval",
        "score": 0.9,
    }

    assert has_valid_evidence_reference("答案引用 [chunk-1]\n\n证据引用：...", [evidence])
    assert not has_valid_evidence_reference("没有引用。", [evidence])


def test_boundary_case_blocks_metadata_leakage():
    """确认不存在 documentType 下的边界样本不能返回其它资料。"""
    case = {"case_id": "B02", "expected_document_ids": []}
    response = {"answer": "当前知识库没有检索到足够相关的证据。", "evidences": []}

    result = evaluate_boundary_case(case, response)

    assert result["passed"]
    assert result["evidence_count"] == 0


def test_boundary_case_treats_low_score_as_insufficient_evidence():
    """确认低相关 evidence 不会被误判为世界杯问题的有效依据。"""
    case = {"case_id": "B01", "expected_document_ids": []}
    response = {
        "answer": "根据知识库，没有检索到足够相关的证据。",
        "evidences": [{"documentId": "llm-ragas-d01", "score": 0.01}],
    }

    result = evaluate_boundary_case(case, response, boundary_score_threshold=0.18)

    assert result["passed"]
    assert result["max_score"] == 0.01


def test_boundary_case_accepts_explicit_llm_refusal_with_evidence():
    """确认真实 LLM 明确拒答时，边界样本不会因返回诊断 evidence 失败。"""
    case = {"case_id": "B01", "expected_document_ids": []}
    response = {
        "answer": "无法回答用户问题。现有 evidence 不涉及烘焙工艺，存在关键信息缺口。",
        "evidences": [{"documentId": "llm-ragas-d02", "score": 0.26}],
    }

    result = evaluate_boundary_case(case, response, boundary_score_threshold=0.18)

    assert result["passed"]
    assert result["max_score"] == 0.26


def test_has_refusal_intent_accepts_evidence_gap_expressions():
    """确认边界评估能识别真实模型常见的证据不足表达。"""
    assert has_refusal_intent("现有 evidence 未提供烘焙工艺资料，缺少 autolyse 水合率依据。")
    assert has_refusal_intent("不能基于当前资料给出确定答案，需要补充专业面包资料。")
    assert not has_refusal_intent("可以参考切块策略、Rerank 和 RAGAS 指标完成回答。")


def test_build_ragas_input_row_keeps_project_auxiliary_fields():
    """确认 Ragas 输入只使用标准字段，同时保留项目辅助核验字段。"""
    case = {
        "case_id": "R01",
        "question": "为什么要评估？",
        "reference": "需要评估风险。",
        "expected_document_ids": ["llm-ragas-d01"],
    }
    response = {
        "answer": "因为存在不确定性。[chunk-1]",
        "evidences": [
            {
                "evidenceId": "chunk-1",
                "documentId": "llm-ragas-d01",
                "snippet": "RAG 应用上线前需要评估。",
            }
        ],
    }

    row = build_ragas_input_row(case, response)

    assert row["user_input"] == "为什么要评估？"
    assert row["response"].startswith("因为存在")
    assert row["retrieved_contexts"] == ["RAG 应用上线前需要评估。"]
    assert row["reference"] == "需要评估风险。"
    assert row["retrieved_context_ids"] == ["chunk-1"]
    assert row["retrieved_document_ids"] == ["llm-ragas-d01"]
    assert row["expected_document_ids"] == ["llm-ragas-d01"]


def test_ragas_mode_reuses_dashscope_key_by_default(monkeypatch):
    """确认真实 Ragas 模式默认复用项目 DASHSCOPE_API_KEY。"""
    monkeypatch.delenv("RAGAS_EVAL_API_KEY", raising=False)
    monkeypatch.delenv("RAGAS_EVAL_BASE_URL", raising=False)
    monkeypatch.delenv("RAGAS_EVAL_LLM_MODEL", raising=False)
    monkeypatch.delenv("RAGAS_EVAL_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    settings = ensure_ragas_eval_config()

    assert settings.api_key == "dashscope-key"
    assert settings.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.llm_model == "qwen-plus"
    assert settings.embedding_model == "text-embedding-v4"


def test_ragas_eval_key_overrides_dashscope_key(monkeypatch):
    """确认显式 RAGAS_EVAL_API_KEY 优先于项目 DASHSCOPE_API_KEY。"""
    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    settings = ensure_ragas_eval_config()

    assert settings.api_key == "eval-key"


def _set_valid_ragas_env(monkeypatch, *, provider: str = "openai-compatible") -> None:
    """设置一组合法的 Ragas 真实评分环境变量。"""
    for name in (
        "RAGAS_EVAL_MAX_RETRIES",
        "RAGAS_EVAL_MAX_WAIT_SECONDS",
        "RAGAS_EVAL_MAX_WORKERS",
        "RAGAS_EVAL_BATCH_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAGAS_EVAL_PROVIDER", provider)
    monkeypatch.setenv("RAGAS_EVAL_API_KEY", "eval-key")
    monkeypatch.setenv("RAGAS_EVAL_LLM_MODEL", "eval-llm")
    monkeypatch.setenv("RAGAS_EVAL_EMBEDDING_MODEL", "eval-embedding")
    monkeypatch.setenv("RAGAS_EVAL_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("RAGAS_EVAL_TEMPERATURE", "0.2")
    if provider == "openai-compatible":
        monkeypatch.setenv("RAGAS_EVAL_BASE_URL", "https://example.test/v1")
    else:
        monkeypatch.delenv("RAGAS_EVAL_BASE_URL", raising=False)


def test_ragas_eval_config_uses_dashscope_base_url_for_compatible(monkeypatch):
    """确认 openai-compatible 模式可默认使用百炼兼容地址。"""
    _set_valid_ragas_env(monkeypatch)
    monkeypatch.delenv("RAGAS_EVAL_BASE_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("RAG_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_EMBEDDING_BASE_URL", raising=False)

    settings = load_ragas_eval_settings()

    assert settings.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_ragas_eval_config_allows_official_openai_without_base_url(monkeypatch):
    """确认 openai 模式可不配置 base_url，并会映射给 Ragas openai provider。"""
    _set_valid_ragas_env(monkeypatch, provider="openai")

    settings = load_ragas_eval_settings()

    assert settings.provider == "openai"
    assert settings.ragas_provider == "openai"
    assert settings.base_url is None


def test_ragas_eval_config_rejects_invalid_provider(monkeypatch):
    """确认 provider 只允许 openai-compatible 或 openai。"""
    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("RAGAS_EVAL_PROVIDER", "dashscope")

    with pytest.raises(RuntimeError, match="RAGAS_EVAL_PROVIDER 只允许"):
        load_ragas_eval_settings()


def test_ragas_eval_config_requires_models_and_valid_numbers(monkeypatch):
    """确认模型支持默认复用，timeout 和 temperature 配置会严格校验。"""
    _set_valid_ragas_env(monkeypatch)
    monkeypatch.delenv("RAGAS_EVAL_LLM_MODEL", raising=False)
    monkeypatch.delenv("RAG_LLM_MODEL", raising=False)
    assert load_ragas_eval_settings().llm_model == "qwen-plus"

    _set_valid_ragas_env(monkeypatch)
    monkeypatch.delenv("RAGAS_EVAL_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("RAG_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_EMBEDDING_MODEL", raising=False)
    assert load_ragas_eval_settings().embedding_model == "text-embedding-v4"

    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("RAGAS_EVAL_TIMEOUT_SECONDS", "abc")
    with pytest.raises(RuntimeError, match="RAGAS_EVAL_TIMEOUT_SECONDS 必须是数字"):
        load_ragas_eval_settings()

    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("RAGAS_EVAL_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="RAGAS_EVAL_TIMEOUT_SECONDS 必须大于 0"):
        load_ragas_eval_settings()

    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("RAGAS_EVAL_MAX_RETRIES", "-1")
    with pytest.raises(RuntimeError, match="RAGAS_EVAL_MAX_RETRIES 必须大于或等于 0"):
        load_ragas_eval_settings()

    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("RAGAS_EVAL_MAX_WORKERS", "0")
    with pytest.raises(RuntimeError, match="RAGAS_EVAL_MAX_WORKERS 必须大于 0"):
        load_ragas_eval_settings()

    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("RAGAS_EVAL_BATCH_SIZE", "0")
    with pytest.raises(RuntimeError, match="RAGAS_EVAL_BATCH_SIZE 必须大于 0"):
        load_ragas_eval_settings()

    _set_valid_ragas_env(monkeypatch)
    monkeypatch.setenv("RAGAS_EVAL_TEMPERATURE", "2.1")
    with pytest.raises(RuntimeError, match="RAGAS_EVAL_TEMPERATURE 必须在 0 到 2 之间"):
        load_ragas_eval_settings()


def test_ragas_eval_config_rejects_invalid_base_url(monkeypatch):
    """确认显式 base_url 必须带 http 或 https 协议。"""
    _set_valid_ragas_env(monkeypatch, provider="openai")
    monkeypatch.setenv("RAGAS_EVAL_BASE_URL", "example.test/v1")

    with pytest.raises(RuntimeError, match="RAGAS_EVAL_BASE_URL 必须以"):
        load_ragas_eval_settings()


def test_parse_ragas_metric_keys_allows_subset_and_aliases():
    """确认 Ragas 指标子集可减少真实评分耗时。"""
    assert parse_ragas_metric_keys(None) == (
        "context_precision",
        "context_recall",
        "faithfulness",
        "answer_relevancy",
    )
    assert parse_ragas_metric_keys("context_recall, answer_relevance, recall") == (
        "context_recall",
        "answer_relevancy",
    )


def test_parse_ragas_metric_keys_rejects_invalid_value():
    """确认 Ragas 指标子集配置错误时有中文提示。"""
    with pytest.raises(RuntimeError, match="RAGAS_EVAL_METRICS 包含不支持的指标"):
        parse_ragas_metric_keys("bad_metric")


def test_require_ragas_dependencies_reports_core_group(monkeypatch):
    """确认缺核心依赖时中文提示会区分 ragas/openai/datasets。"""
    original_import_module = __import__("importlib").import_module

    def fake_import_module(name, package=None):
        if name in {"ragas", "openai", "datasets"}:
            raise ImportError(name)
        return original_import_module(name, package)

    monkeypatch.setattr("evaluation.ragas_eval_common.importlib.import_module", fake_import_module)

    with pytest.raises(RuntimeError) as exc_info:
        require_ragas_core_dependencies()

    message = str(exc_info.value)
    assert "ragas、openai、datasets" in message
    assert "python -m pip install -r ai-python/requirements.txt" in message


class _FakeMetric:
    """测试用假指标，记录构造参数并暴露 name。"""

    def __init__(self, llm=None, embeddings=None, name=None):
        self.llm = llm
        self.embeddings = embeddings
        self.name = name or self.__class__.__name__


class ContextPrecisionWithReference(_FakeMetric):
    pass


class ContextRecall(_FakeMetric):
    pass


class Faithfulness(_FakeMetric):
    pass


class AnswerRelevancy(_FakeMetric):
    pass


class LLMContextPrecisionWithReference(_FakeMetric):
    pass


class LLMContextRecall(_FakeMetric):
    pass


class LegacyFaithfulness(_FakeMetric):
    pass


class LegacyAnswerRelevancy(_FakeMetric):
    pass


class ResponseRelevancy(_FakeMetric):
    pass


def _settings() -> RagasEvalSettings:
    """构造测试用 Ragas 配置。"""
    return RagasEvalSettings(
        provider="openai-compatible",
        ragas_provider="openai",
        base_url="https://example.test/v1",
        api_key="eval-key",
        llm_model="eval-llm",
        embedding_model="eval-embedding",
        timeout_seconds=30,
        max_retries=2,
        max_wait_seconds=10,
        max_workers=2,
        batch_size=1,
        temperature=0.1,
        max_tokens=None,
        metric_keys=("context_precision", "context_recall", "faithfulness", "answer_relevancy"),
    )


def _install_modern_ragas_modules(monkeypatch, *, llm_supports_client: bool = True) -> dict[str, object]:
    """安装测试用现代 Ragas 模块。"""
    calls: dict[str, object] = {}

    class OpenAI:
        def __init__(self, **kwargs):
            calls["client_kwargs"] = kwargs

    class OpenAIEmbeddings:
        def __init__(self, **kwargs):
            calls["embedding_kwargs"] = kwargs

        def embed_text(self, text, **kwargs):
            return [1.0]

        async def aembed_text(self, text, **kwargs):
            return [1.0]

        def embed_texts(self, texts, **kwargs):
            return [[1.0] for _ in texts]

        async def aembed_texts(self, texts, **kwargs):
            return [[1.0] for _ in texts]

    if llm_supports_client:
        def llm_factory(model, provider="openai", client=None, **kwargs):
            calls["llm_factory"] = {"model": model, "provider": provider, "client": client, **kwargs}
            return "modern-llm"
    else:
        def llm_factory(model, provider="openai", **kwargs):
            calls["llm_factory"] = {"model": model, "provider": provider, **kwargs}
            return "legacy-signature-llm"

    openai_module = types.ModuleType("openai")
    openai_module.OpenAI = OpenAI
    embeddings_module = types.ModuleType("ragas.embeddings")
    embeddings_module.OpenAIEmbeddings = OpenAIEmbeddings
    llms_module = types.ModuleType("ragas.llms")
    llms_module.llm_factory = llm_factory
    metrics_module = types.ModuleType("ragas.metrics")
    metrics_module.LLMContextPrecisionWithReference = LLMContextPrecisionWithReference
    metrics_module.LLMContextRecall = LLMContextRecall
    metrics_module.Faithfulness = LegacyFaithfulness
    metrics_module.AnswerRelevancy = LegacyAnswerRelevancy
    for name, module in {
        "openai": openai_module,
        "ragas.embeddings": embeddings_module,
        "ragas.llms": llms_module,
        "ragas.metrics": metrics_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def _install_legacy_ragas_modules(monkeypatch) -> dict[str, object]:
    """安装测试用 legacy Ragas 与 langchain-openai 模块。"""
    calls: dict[str, object] = {}

    class ChatOpenAI:
        def __init__(self, **kwargs):
            calls["chat_kwargs"] = kwargs

    class OpenAIEmbeddings:
        def __init__(self, **kwargs):
            calls["embedding_kwargs"] = kwargs

    class LangchainLLMWrapper:
        def __init__(self, llm, run_config=None):
            self.llm = llm
            self.run_config = run_config

    class LangchainEmbeddingsWrapper:
        def __init__(self, embeddings, run_config=None):
            self.embeddings = embeddings
            self.run_config = run_config

    langchain_openai = types.ModuleType("langchain_openai")
    langchain_openai.ChatOpenAI = ChatOpenAI
    langchain_openai.OpenAIEmbeddings = OpenAIEmbeddings
    llms_module = sys.modules.get("ragas.llms") or types.ModuleType("ragas.llms")
    llms_module.LangchainLLMWrapper = LangchainLLMWrapper
    embeddings_module = sys.modules.get("ragas.embeddings") or types.ModuleType("ragas.embeddings")
    embeddings_module.LangchainEmbeddingsWrapper = LangchainEmbeddingsWrapper
    metrics_module = types.ModuleType("ragas.metrics")
    metrics_module.LLMContextPrecisionWithReference = LLMContextPrecisionWithReference
    metrics_module.LLMContextRecall = LLMContextRecall
    metrics_module.Faithfulness = Faithfulness
    metrics_module.ResponseRelevancy = ResponseRelevancy
    for name, module in {
        "langchain_openai": langchain_openai,
        "ragas.llms": llms_module,
        "ragas.embeddings": embeddings_module,
        "ragas.metrics": metrics_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_build_modern_adapter_uses_evaluate_compatible_metrics(monkeypatch):
    """确认现代主路径使用 OpenAI client 和 evaluate 兼容指标。"""
    calls = _install_modern_ragas_modules(monkeypatch)

    adapter = build_ragas_model_adapter(_settings())

    assert adapter.adapter_name == "modern"
    assert adapter.metric_names == [
        "LLMContextPrecisionWithReference",
        "LLMContextRecall",
        "LegacyFaithfulness",
        "LegacyAnswerRelevancy",
    ]
    assert calls["client_kwargs"]["base_url"] == "https://example.test/v1"
    assert calls["llm_factory"]["provider"] == "openai"
    assert calls["llm_factory"]["temperature"] == 0.1


def test_ragas_run_config_uses_conservative_retry_and_worker_settings(monkeypatch):
    """确认真实 Ragas 运行配置不会默认高并发和长重试。"""
    captured = {}

    class RunConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    run_config_module = types.ModuleType("ragas.run_config")
    run_config_module.RunConfig = RunConfig
    monkeypatch.setitem(sys.modules, "ragas.run_config", run_config_module)

    config = _build_run_config(_settings())

    assert config is not None
    assert captured == {"timeout": 30, "max_retries": 2, "max_wait": 10, "max_workers": 2}


def test_call_ragas_evaluate_passes_batch_size():
    """确认 batch_size 会传给 Ragas evaluate，便于降低评估并发压力。"""
    captured = {}

    class RagasModule:
        @staticmethod
        def evaluate(**kwargs):
            captured.update(kwargs)
            return {"faithfulness": [1.0]}

    result = _call_ragas_evaluate(
        RagasModule,
        dataset="dataset",
        adapter=types.SimpleNamespace(adapter_name="legacy", metrics=["metric"], llm="llm", embeddings="embeddings"),
        run_config="run-config",
        batch_size=1,
    )

    assert result == {"faithfulness": [1.0]}
    assert captured["batch_size"] == 1
    assert captured["run_config"] == "run-config"


def test_build_legacy_adapter_only_after_modern_signature_fails(monkeypatch):
    """确认现代 llm_factory 不支持 client 时才回退 legacy wrapper 指标。"""
    _install_modern_ragas_modules(monkeypatch, llm_supports_client=False)
    calls = _install_legacy_ragas_modules(monkeypatch)

    adapter = build_ragas_model_adapter(_settings())

    assert adapter.adapter_name == "legacy"
    assert adapter.metric_names == [
        "LLMContextPrecisionWithReference",
        "LLMContextRecall",
        "Faithfulness",
        "ResponseRelevancy",
    ]
    assert calls["chat_kwargs"]["base_url"] == "https://example.test/v1"
    assert calls["chat_kwargs"]["temperature"] == 0.1
    assert any(error.startswith("modern:") for error in adapter.construction_errors)


def test_legacy_missing_dependency_has_clear_hint(monkeypatch):
    """确认 legacy fallback 缺 langchain-openai 时提示 fallback 依赖。"""
    _install_modern_ragas_modules(monkeypatch, llm_supports_client=False)
    monkeypatch.setitem(sys.modules, "langchain_openai", None)

    with pytest.raises(RuntimeError) as exc_info:
        build_ragas_model_adapter(_settings())

    assert "fallback 需要 langchain-openai" in str(exc_info.value)


class _ResultWithPandas:
    """测试用 to_pandas 结果对象。"""

    def to_pandas(self):
        import pandas as pd

        return pd.DataFrame(
            [
                {"case_id": "old", "user_input": "问题1", "faithfulness": 1.0},
                {"case_id": "old", "user_input": "问题2", "faithfulness": 0.5},
            ]
        )


class _ResultWithScores:
    """测试用 scores 结果对象。"""

    scores = [{"faithfulness": 0.8}, {"faithfulness": 0.6}]


def _ragas_rows() -> list[dict[str, object]]:
    """构造测试用 Ragas 输入行。"""
    return [{"case_id": "R01"}, {"case_id": "R02"}]


def test_ragas_result_to_rows_supports_to_pandas_scores_dict_and_list():
    """确认不同 Ragas 结果形态都能转换为行列表。"""
    assert ragas_result_to_rows(_ResultWithPandas())[0]["faithfulness"] == 1.0
    assert ragas_result_to_rows(_ResultWithScores())[1]["faithfulness"] == 0.6
    assert ragas_result_to_rows({"faithfulness": [0.7, 0.9]}) == [{"faithfulness": 0.7}, {"faithfulness": 0.9}]
    assert ragas_result_to_rows([{"faithfulness": 0.7}]) == [{"faithfulness": 0.7}]


def test_write_ragas_scores_csv_overwrites_case_id_and_summarizes_numeric_only(tmp_path):
    """确认写 CSV 前会覆盖已有 case_id，且汇总跳过文本列。"""
    output_csv = tmp_path / "ragas_scores.csv"

    summary = write_ragas_scores_csv(output_csv, _ResultWithPandas(), _ragas_rows())

    content = output_csv.read_text(encoding="utf-8-sig")
    assert "R01" in content
    assert "old" not in content
    assert summary == {"faithfulness": 0.75}


def test_write_ragas_scores_csv_rejects_row_count_mismatch(tmp_path):
    """确认 Ragas 输出行数和输入样本数不一致时会中文报错。"""
    output_csv = tmp_path / "ragas_scores.csv"

    with pytest.raises(RuntimeError, match="Ragas 结果行数与输入样本数不一致"):
        write_ragas_scores_csv(output_csv, [{"faithfulness": 1.0}], _ragas_rows())


def test_ragas_cli_missing_config_writes_ragas_outputs_without_offline_scores(monkeypatch, tmp_path):
    """确认 --mode ragas 缺配置时只写 Ragas 输入和 failureReason，不运行离线指标。"""
    output_dir = tmp_path / "ragas-missing-config"
    monkeypatch.setattr(
        run_ragas_small_eval,
        "parse_args",
        lambda: types.SimpleNamespace(
            mode="ragas",
            cases=Path("unused-cases.jsonl"),
            documents=Path("unused-documents.json"),
            rag_profile="current",
            skip_index=False,
            case_id=None,
            case_index=None,
            output_dir=output_dir,
        ),
    )
    monkeypatch.setattr(
        run_ragas_small_eval,
        "run_project_ragas_input",
        lambda **kwargs: EvaluationRunResult(
            rows=[],
            ragas_rows=[
                {
                    "case_id": "R01",
                    "user_input": "问题",
                    "response": "回答",
                    "retrieved_contexts": ["证据"],
                    "reference": "参考",
                }
            ],
            summary={"main_case_count": 1, "ragas_input_count": 1},
        ),
    )
    monkeypatch.delenv("RAGAS_EVAL_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    exit_code = run_ragas_small_eval.main()

    assert exit_code == 1
    assert (output_dir / "ragas_input.jsonl").exists()
    assert not (output_dir / "offline_scores.csv").exists()
    assert not (output_dir / "manual_review.md").exists()
    assert not (output_dir / "ragas_scores.csv").exists()
    run_config = json.loads((output_dir / "run_config.json").read_text(encoding="utf-8"))
    assert "RAGAS_EVAL_API_KEY 或 DASHSCOPE_API_KEY 未配置" in run_config["ragas"]["failureReason"]
    assert "summary" in run_config
    assert "ragas_failure_reason" in run_config["summary"]


def test_ragas_cli_skip_index_only_queries_existing_index(monkeypatch, tmp_path):
    """确认真实 Ragas 模式可复用已有 Ragas_Test 索引，不重复索引资料。"""
    output_dir = tmp_path / "ragas-skip-index"
    captured_kwargs = {}
    monkeypatch.setattr(
        run_ragas_small_eval,
        "parse_args",
        lambda: types.SimpleNamespace(
            mode="ragas",
            cases=Path("unused-cases.jsonl"),
            documents=Path("unused-documents.json"),
            rag_profile="current",
            skip_index=True,
            case_id="R03",
            case_index=None,
            output_dir=output_dir,
        ),
    )

    def fake_run_project_ragas_input(**kwargs):
        captured_kwargs.update(kwargs)
        return EvaluationRunResult(
            rows=[],
            ragas_rows=[
                {
                    "case_id": "R01",
                    "user_input": "问题",
                    "response": "回答",
                    "retrieved_contexts": ["证据"],
                    "reference": "参考",
                }
            ],
            summary={"main_case_count": 1, "ragas_input_count": 1, "index_documents": False},
        )

    monkeypatch.setattr(run_ragas_small_eval, "run_project_ragas_input", fake_run_project_ragas_input)
    monkeypatch.setattr(
        run_ragas_small_eval,
        "load_ragas_eval_settings",
        lambda: RagasEvalSettings(
            provider="openai-compatible",
            ragas_provider="openai",
            base_url="https://example.test/v1",
            api_key="eval-key",
            llm_model="eval-llm",
            embedding_model="eval-embedding",
            timeout_seconds=30,
            max_retries=2,
            max_wait_seconds=10,
            max_workers=2,
            batch_size=1,
            temperature=0,
            max_tokens=None,
            metric_keys=("answer_relevancy",),
        ),
    )
    monkeypatch.setattr(
        run_ragas_small_eval,
        "run_ragas_metrics",
        lambda rows, output_csv, settings: types.SimpleNamespace(
            summary={"answer_relevancy": 0.8},
            ragas_version="0.4.3",
            model_adapter="modern",
            metric_names=["answer_relevancy"],
        ),
    )

    exit_code = run_ragas_small_eval.main()

    assert exit_code == 0
    assert captured_kwargs["index_documents"] is False
    assert captured_kwargs["case_id"] == "R03"
    assert captured_kwargs["case_index"] is None
    assert (output_dir / "ragas_input.jsonl").exists()
    assert not (output_dir / "offline_scores.csv").exists()
    run_config = json.loads((output_dir / "run_config.json").read_text(encoding="utf-8"))
    assert run_config["summary"]["index_documents"] is False


def test_ragas_cli_invalid_case_selection_returns_usage_error(monkeypatch, tmp_path):
    """确认单样本筛选参数错误时不会继续调用 RAG 或 Ragas。"""
    monkeypatch.setattr(
        run_ragas_small_eval,
        "parse_args",
        lambda: types.SimpleNamespace(
            mode="ragas",
            cases=Path("unused-cases.jsonl"),
            documents=Path("unused-documents.json"),
            rag_profile="current",
            skip_index=True,
            case_id="R01",
            case_index=1,
            output_dir=tmp_path,
        ),
    )

    def fake_run_project_ragas_input(**kwargs):
        raise ValueError("--case-id 和 --case-index 只能二选一。")

    monkeypatch.setattr(run_ragas_small_eval, "run_project_ragas_input", fake_run_project_ragas_input)

    assert run_ragas_small_eval.main() == 2


def test_skip_index_rejects_offline_mode(monkeypatch, tmp_path):
    """确认 --skip-index 不会误用于离线指标模式。"""
    monkeypatch.setattr(
        run_ragas_small_eval,
        "parse_args",
        lambda: types.SimpleNamespace(
            mode="offline",
            cases=Path("unused-cases.jsonl"),
            documents=Path("unused-documents.json"),
            rag_profile="current",
            skip_index=True,
            case_id=None,
            case_index=None,
            output_dir=tmp_path,
        ),
    )

    assert run_ragas_small_eval.main() == 2
