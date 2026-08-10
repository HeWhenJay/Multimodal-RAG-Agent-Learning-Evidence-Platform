"""复习卡片 PAE/ReAct LangGraph 的循环与人工终态测试。"""

import pytest

from app.review.generation_graph import (
    DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS,
    DEFAULT_REVIEW_GRAPH_MAX_MERGE_ROUNDS,
    REVIEW_GRAPH_RECURSION_LIMIT,
    ReviewManualReviewRequired,
    configured_review_graph_max_attempts,
    configured_review_graph_max_merge_rounds,
    run_review_generation_graph,
)


class GateError(RuntimeError):
    """模拟质量观察节点返回的逐项门禁反馈。"""

    diagnostics = ("问题不是完整疑问句", "answer 未被 evidence 支撑")


def test_quality_feedback_is_sent_to_next_actor_attempt() -> None:
    """第一次观察失败时，第二次 actor 必须收到同一轮修复诊断。"""
    calls: list[tuple[int, list[str]]] = []

    def actor(attempt: int, feedback: list[str], _previous_candidate: dict, _curator_context: dict) -> dict:
        calls.append((attempt, feedback))
        return {"attempt": attempt}

    def observer(candidate: dict, _curator_context: dict) -> dict:
        if candidate["attempt"] == 1:
            raise GateError("质量门禁失败")
        return {"ok": True}

    outcome = run_review_generation_graph(actor=actor, observer=observer, plan={"materialId": 12}, max_attempts=4)

    assert outcome.attempts == 2
    assert calls[0] == (1, [])
    assert calls[1][0] == 2
    assert calls[1][1] == ["问题不是完整疑问句", "answer 未被 evidence 支撑"]
    assert "第 1 次：问题不是完整疑问句" in outcome.quality_feedback


def test_repair_actor_receives_previous_candidate_for_incremental_fix() -> None:
    """第二轮必须拿到上一版候选，以便保留合格卡并只修复坏卡。"""
    previous_candidates: list[dict] = []

    def actor(attempt: int, _feedback: list[str], previous_candidate: dict, _curator_context: dict) -> dict:
        previous_candidates.append(previous_candidate)
        return {"attempt": attempt, "cards": [{"question": f"第 {attempt} 版"}]}

    def observer(candidate: dict, _curator_context: dict) -> dict:
        if candidate["attempt"] == 1:
            raise GateError("第一版含坏卡")
        return {"ok": True}

    run_review_generation_graph(actor=actor, observer=observer, plan={}, max_attempts=2)

    assert previous_candidates[0] == {}
    assert previous_candidates[1]["attempt"] == 1
    assert previous_candidates[1]["cards"] == [{"question": "第 1 版"}]


def test_exhausted_quality_repair_enters_manual_review() -> None:
    """连续质量失败不能无限调用模型，必须稳定进入人工处理。"""
    calls = 0

    def actor(attempt: int, _feedback: list[str], _previous_candidate: dict, _curator_context: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"attempt": attempt}

    def observer(_candidate: dict, _curator_context: dict) -> dict:
        raise GateError("结构化原始问题覆盖不足")

    with pytest.raises(ReviewManualReviewRequired) as raised:
        run_review_generation_graph(actor=actor, observer=observer, plan={}, max_attempts=3)

    assert calls == 3
    assert raised.value.attempts == 3
    assert any("问题不是完整疑问句" in item for item in raised.value.quality_feedback)


def test_review_graph_keeps_large_recursion_limit_separate_from_model_budget() -> None:
    """999 只用于 LangGraph 递归保护，模型预算仍由独立参数控制。"""
    assert REVIEW_GRAPH_RECURSION_LIMIT == 999


def test_review_graph_uses_eight_quality_attempts_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认质量修复预算提升到 8 次，同时继续允许环境变量覆盖。"""
    monkeypatch.delenv("REVIEW_GENERATION_MAX_ATTEMPTS", raising=False)
    assert DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS == 8
    assert configured_review_graph_max_attempts() == 8


def test_review_graph_reports_real_nodes_attempts_and_terminal_progress() -> None:
    """复习图必须上报真实节点、模型轮次、修复反馈和最终保存阶段。"""
    events: list[dict] = []

    def actor(attempt: int, _feedback: list[str], _previous_candidate: dict, _curator_context: dict) -> dict:
        return {"attempt": attempt}

    def observer(candidate: dict, _curator_context: dict) -> dict:
        if candidate["attempt"] == 1:
            raise GateError("第一次质量门禁失败")
        return {"ok": True}

    outcome = run_review_generation_graph(
        actor=actor,
        observer=observer,
        plan={"structuredQuestionCount": 20, "maxCards": 20},
        max_attempts=3,
        on_progress=events.append,
    )

    assert outcome.attempts == 2
    assert [event["stageCode"] for event in events] == [
        "review.planner",
        "review.actor",
        "review.observer",
        "review.repair",
        "review.actor",
        "review.observer",
        "review.multi_card_observer",
        "review.persist",
    ]
    assert [event["attempt"] for event in events if event["stageCode"] == "review.actor"] == [1, 2]
    assert [event["percent"] for event in events] == sorted(event["percent"] for event in events)
    assert events[-1]["percent"] == 94


def card(
    index: int,
    *,
    question: str | None = None,
    knowledge_unit_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> dict:
    """构造包含合并守恒字段的离线卡片候选。"""
    source_question = f"原始问题{index}是什么？"
    return {
        "question": question or f"问题{index}是什么？",
        "sourceQuestion": source_question,
        "sourceQuestions": [source_question],
        "coveredSourceQuestionKeys": [f"原始问题{index}是什么"],
        "knowledgeUnitIds": knowledge_unit_ids or [f"KU-{index}"],
        "answer": f"答案{index}",
        "hint": f"从方向{index}回忆",
        "evidenceIds": evidence_ids or [f"e-{index}"],
    }


def merge_plan(indexes: list[int], cards: list[dict], *, target_question: str = "并列策略有哪些？") -> dict:
    """根据点名卡片构造完整、可确定性校验的合并计划。"""
    selected = [cards[index - 1] for index in indexes]
    return {
        "passed": False,
        "mergeGroups": [
            {
                "cardIndexes": indexes,
                "parentTopic": "并列策略",
                "reason": "属于同一栏目和同一回忆路径",
                "targetQuestion": target_question,
                "hintTopics": ["按策略名称和作用回忆"],
                "mustPreserveKnowledgeUnitIds": [
                    item for current in selected for item in current["knowledgeUnitIds"]
                ],
                "mustPreserveEvidenceIds": [item for current in selected for item in current["evidenceIds"]],
                "mustPreserveClaims": [
                    claim
                    for current in selected
                    for claim in current["answer"].strip("。").split("。")
                    if claim
                ],
            }
        ],
    }


def merge_payload(plan: dict, candidate: dict) -> dict:
    """按照计划合并全部字段，模拟 Merge Repair 的结构化响应。"""
    group = plan["mergeGroups"][0]
    selected = [candidate["cards"][index - 1] for index in group["cardIndexes"]]
    source_questions = [item for current in selected for item in current.get("sourceQuestions", [])]
    covered_keys = [item for current in selected for item in current.get("coveredSourceQuestionKeys", [])]
    return {
        "mergedGroups": [
            {
                "cardIndexes": group["cardIndexes"],
                "card": {
                    "question": group["targetQuestion"],
                    "sourceQuestion": None,
                    "sourceQuestions": source_questions,
                    "coveredSourceQuestionKeys": covered_keys,
                    "knowledgeUnitIds": group["mustPreserveKnowledgeUnitIds"],
                    "answer": "。".join(group["mustPreserveClaims"]) + "。",
                    "hint": "从策略名称和作用两个方向回忆",
                    "evidenceIds": group["mustPreserveEvidenceIds"],
                },
            }
        ]
    }


def test_multi_card_observer_first_pass_saves_directly() -> None:
    """多卡片首次复查通过时直接保存，不进入 Merge Repair。"""
    events: list[dict] = []
    merge_calls = 0
    candidate = {"summary": "完整摘要", "cards": [card(1), card(2)]}

    def forbidden_merge(*_args) -> dict:
        nonlocal merge_calls
        merge_calls += 1
        return {}

    outcome = run_review_generation_graph(
        actor=lambda *_args: candidate,
        observer=lambda current, _context: current,
        multi_card_observer=lambda _current, _context, _round: {"passed": True, "mergeGroups": []},
        merge_repair=forbidden_merge,
        plan={},
        on_progress=events.append,
    )

    assert outcome.result == candidate
    assert merge_calls == 0
    assert [event["stageCode"] for event in events[-2:]] == [
        "review.multi_card_observer",
        "review.persist",
    ]


def test_merge_plan_enters_merge_repair_then_rechecks_both_observers() -> None:
    """发现合并计划后必须依次回到单卡门禁和多卡复查。"""
    events: list[dict] = []
    observed_card_counts: list[int] = []
    candidate = {"summary": "完整摘要", "cards": [card(1), card(2), card(3)]}

    def observer(current: dict, _context: dict) -> dict:
        observed_card_counts.append(len(current["cards"]))
        return current

    def multi(current: dict, _context: dict, merge_round: int) -> dict:
        return merge_plan([1, 2], current["cards"]) if merge_round == 0 else {"passed": True, "mergeGroups": []}

    outcome = run_review_generation_graph(
        actor=lambda *_args: candidate,
        observer=observer,
        multi_card_observer=multi,
        merge_repair=lambda current, plan, _context, _round: merge_payload(plan, current),
        plan={},
        on_progress=events.append,
    )

    assert observed_card_counts == [3, 2]
    assert [event["stageCode"] for event in events].count("review.multi_card_observer") == 2
    assert "review.merge_repair" in [event["stageCode"] for event in events]
    assert len(outcome.result["cards"]) == 2


def test_multiple_merge_rounds_continue_until_multi_card_observer_passes() -> None:
    """多卡片 Observer 可连续提出不同合并组，直到整组粒度通过。"""
    candidate = {"summary": "完整摘要", "cards": [card(1), card(2), card(3), card(4)]}
    rounds: list[int] = []

    def multi(current: dict, _context: dict, merge_round: int) -> dict:
        rounds.append(merge_round)
        return merge_plan([1, 2], current["cards"]) if len(current["cards"]) > 2 else {"passed": True, "mergeGroups": []}

    outcome = run_review_generation_graph(
        actor=lambda *_args: candidate,
        observer=lambda current, _context: current,
        multi_card_observer=multi,
        merge_repair=lambda current, plan, _context, _round: merge_payload(plan, current),
        plan={},
        max_merge_rounds=4,
    )

    assert rounds == [0, 1, 2]
    assert len(outcome.result["cards"]) == 2
    assert outcome.attempts == 1


def test_merge_round_exhaustion_enters_safe_manual_review() -> None:
    """合并轮次耗尽时进入 NEEDS_REVIEW，并保留最后一次完整有效候选。"""
    candidate = {"summary": "完整摘要", "cards": [card(1), card(2), card(3)]}

    with pytest.raises(ReviewManualReviewRequired) as raised:
        run_review_generation_graph(
            actor=lambda *_args: candidate,
            observer=lambda current, _context: current,
            multi_card_observer=lambda current, _context, _round: merge_plan([1, 2], current["cards"]),
            merge_repair=lambda current, plan, _context, _round: merge_payload(plan, current),
            plan={},
            max_merge_rounds=1,
        )

    assert raised.value.attempts == 1
    assert len(raised.value.last_valid_candidate["cards"]) == 2
    assert any("最大 1 轮" in item for item in raised.value.quality_feedback)


def test_unchanged_candidate_fingerprint_stops_non_converging_merge() -> None:
    """问题集合、知识单元和 evidence 均无变化时立即停止无进展循环。"""
    first = card(1, question="问题一是什么？", knowledge_unit_ids=["KU-1"], evidence_ids=["e-1"])
    second = card(2, question="问题二是什么？", knowledge_unit_ids=["KU-2"], evidence_ids=["e-2"])
    duplicate = card(3, question="问题二是什么？", knowledge_unit_ids=["KU-2"], evidence_ids=["e-2"])
    candidate = {"summary": "完整摘要", "cards": [first, second, duplicate]}

    with pytest.raises(ReviewManualReviewRequired) as raised:
        run_review_generation_graph(
            actor=lambda *_args: candidate,
            observer=lambda current, _context: current,
            multi_card_observer=lambda current, _context, _round: merge_plan(
                [1, 2], current["cards"], target_question="问题一是什么？"
            ),
            merge_repair=lambda current, plan, _context, _round: merge_payload(plan, current),
            plan={},
        )

    assert any("指纹连续无变化" in item for item in raised.value.quality_feedback)
    assert raised.value.last_valid_candidate == candidate


def test_merge_preserves_unions_and_untouched_cards_exactly() -> None:
    """合并卡保留多个知识单元、原问题与 evidence，并确保未点名卡完全不变。"""
    untouched = card(3)
    candidate = {"summary": "完整摘要", "cards": [card(1), card(2), untouched]}

    outcome = run_review_generation_graph(
        actor=lambda *_args: candidate,
        observer=lambda current, _context: current,
        multi_card_observer=lambda current, _context, merge_round: (
            merge_plan([1, 2], current["cards"]) if merge_round == 0 else {"passed": True, "mergeGroups": []}
        ),
        merge_repair=lambda current, plan, _context, _round: merge_payload(plan, current),
        plan={},
    )

    merged, preserved = outcome.result["cards"]
    assert merged["knowledgeUnitIds"] == ["KU-1", "KU-2"]
    assert merged["sourceQuestions"] == ["原始问题1是什么？", "原始问题2是什么？"]
    assert merged["evidenceIds"] == ["e-1", "e-2"]
    assert preserved == untouched


def test_default_merge_round_budget_is_four(monkeypatch: pytest.MonkeyPatch) -> None:
    """合并预算默认 4 轮，并允许使用独立环境变量覆盖。"""
    monkeypatch.delenv("REVIEW_GENERATION_MAX_MERGE_ROUNDS", raising=False)
    assert DEFAULT_REVIEW_GRAPH_MAX_MERGE_ROUNDS == 4
    assert configured_review_graph_max_merge_rounds() == 4


def test_langextract_curator_runs_once_and_is_shared_by_actor_and_observer() -> None:
    """线上 Curator 只执行一次，后续修复轮次复用同一批严格定位候选。"""
    events: list[dict] = []
    curator_calls = 0
    actor_contexts: list[dict] = []
    observer_contexts: list[dict] = []

    def curator() -> dict:
        nonlocal curator_calls
        curator_calls += 1
        return {
            "status": "COMPLETED",
            "knowledgeUnits": [{"knowledgeUnitId": "KU-001", "text": "页缓存减少磁盘读取", "evidenceIds": ["e-1"]}],
            "selectedKnowledgeUnitCount": 1,
            "rawCandidateCount": 2,
            "acceptedCandidateCount": 1,
            "requestCount": 2,
        }

    def actor(attempt: int, _feedback: list[str], _previous: dict, context: dict) -> dict:
        actor_contexts.append(context)
        return {"attempt": attempt}

    def observer(candidate: dict, context: dict) -> dict:
        observer_contexts.append(context)
        if candidate["attempt"] == 1:
            raise GateError("首轮失败")
        return {"ok": True}

    outcome = run_review_generation_graph(
        curator=curator,
        actor=actor,
        observer=observer,
        plan={"maxCards": 8},
        max_attempts=2,
        on_progress=events.append,
    )

    assert outcome.attempts == 2
    assert curator_calls == 1
    assert all(context["knowledgeUnits"][0]["knowledgeUnitId"] == "KU-001" for context in actor_contexts)
    assert observer_contexts == actor_contexts
    assert [event["stageCode"] for event in events[:3]] == [
        "review.planner",
        "review.curator",
        "review.curator",
    ]
    assert [event["percent"] for event in events] == sorted(event["percent"] for event in events)
