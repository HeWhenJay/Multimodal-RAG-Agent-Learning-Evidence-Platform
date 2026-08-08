"""在真实复习资料上运行当前 Curator 与官方 LangExtract 的 A/B。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

try:
    from rag.evaluation.review_curator_ab_common import (
        CuratorCaseResult,
        DEFAULT_CASES_PATH,
        case_result_to_dict,
        decide_langextract,
        load_cases,
        load_material_evidences,
        run_baseline_arm,
        run_langextract_arm,
        select_cases,
    )
    from app.review.knowledge_extractor import review_llm_model
except ModuleNotFoundError:
    from review_curator_ab_common import (  # type: ignore[no-redef]
        CuratorCaseResult,
        DEFAULT_CASES_PATH,
        case_result_to_dict,
        decide_langextract,
        load_cases,
        load_material_evidences,
        run_baseline_arm,
        run_langextract_arm,
        select_cases,
    )
    from review.knowledge_extractor import review_llm_model  # type: ignore[no-redef]


def parse_args() -> argparse.Namespace:
    """解析 A/B 输入、预算和输出目录。"""
    parser = argparse.ArgumentParser(description="运行复习 Knowledge Curator 与 LangExtract A/B")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--extraction-passes", type=int, default=2)
    parser.add_argument("--max-char-buffer", type=int, default=8000)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp") / "review-curator-ab" / datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    return parser.parse_args()


def main() -> int:
    """顺序运行两臂，写入逐例 JSON、汇总 JSON 与中文决策报告。"""
    args = parse_args()
    if not 1 <= args.max_requests <= 64:
        raise ValueError("--max-requests 必须在 1 到 64 之间")
    if not 1 <= args.max_workers <= 10:
        raise ValueError("--max-workers 必须在 1 到 10 之间")
    cases = select_cases(load_cases(args.cases), args.case_ids)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[CuratorCaseResult] = []
    for case in cases:
        material, evidences = load_material_evidences(case)
        print(f"[A/B] {case['caseId']}：运行当前 Curator", flush=True)
        baseline = run_baseline_arm(material, evidences, case, max_requests=args.max_requests)
        print(f"[A/B] {case['caseId']}：运行 LangExtract 1.6", flush=True)
        langextract = run_langextract_arm(
            material,
            evidences,
            case,
            max_requests=args.max_requests,
            extraction_passes=args.extraction_passes,
            max_char_buffer=args.max_char_buffer,
            max_workers=args.max_workers,
        )
        result = CuratorCaseResult(
            case_id=str(case["caseId"]),
            category=str(case.get("category") or "unknown"),
            material_id=material.id,
            title=material.title,
            evidence_count=len(evidences),
            evidence_character_count=sum(len(item.snippet) for item in evidences),
            baseline=baseline,
            langextract=langextract,
        )
        results.append(result)
        write_json(args.output_dir / f"{result.case_id}.json", case_result_to_dict(result))
    decision = decide_langextract(results)
    summary = {
        "config": {
            "casesPath": str(args.cases),
            "caseIds": [result.case_id for result in results],
            "sameModel": review_llm_model(),
            "maxRequestsPerArmPerCase": args.max_requests,
            "langextractVersion": "1.6.x",
            "extractionPasses": args.extraction_passes,
            "maxCharBuffer": args.max_char_buffer,
            "maxWorkers": args.max_workers,
        },
        "decision": decision,
        "cases": [case_result_to_dict(result) for result in results],
    }
    write_json(args.output_dir / "summary.json", summary)
    (args.output_dir / "report.md").write_text(render_report(results, decision), encoding="utf-8")
    print(json.dumps(decision, ensure_ascii=False, indent=2), flush=True)
    print(f"报告：{args.output_dir / 'report.md'}", flush=True)
    return 0


def write_json(path: Path, value: object) -> None:
    """使用 UTF-8 和稳定缩进写入实验产物。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def render_report(results: list[CuratorCaseResult], decision: dict[str, object]) -> str:
    """生成人工可审计的中文 A/B 决策报告。"""
    lines = [
        "# 复习 Knowledge Curator × LangExtract A/B 报告",
        "",
        "两臂使用同一批真实 evidence、同一复习模型和相同最大请求数。",
        "A 臂为当前生产 KnowledgePointExtractor；B 臂为官方 LangExtract 1.6 的分块、多轮抽取与原文定位。",
        "人工金标使用冻结别名做确定性匹配，未额外调用裁判模型。",
        "",
        "| 用例 | A 召回 | B 召回 | A/B Token | A/B 请求 | B 原文映射 | 结论 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in results:
        lines.append(
            f"| {item.case_id} | {item.baseline.expected_recall:.1%} | {item.langextract.expected_recall:.1%} | "
            f"{item.baseline.total_tokens}/{item.langextract.total_tokens} | "
            f"{item.baseline.request_count}/{item.langextract.request_count} | "
            f"{item.langextract.evidence_mapping_success_rate:.1%} | "
            f"{item.baseline.status}/{item.langextract.status} |"
        )
    lines.extend(
        [
            "",
            "## 生产决策",
            "",
            "启用 LangExtract：" + ("是" if decision.get("enable") else "否"),
            "",
        ]
    )
    reasons = list(decision.get("reasons") or [])
    if reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- 所有预注册门槛均通过，可只在候选发现阶段启用，仍保留现有 evidence 和发布门禁。")
    lines.extend(["", "## 逐例遗漏", ""])
    for item in results:
        lines.extend(
            [
                f"### {item.case_id}",
                "",
                f"- A 缺少：{'、'.join(item.baseline.missing_expected_points) or '无'}",
                f"- B 缺少：{'、'.join(item.langextract.missing_expected_points) or '无'}",
                f"- A 错误：{item.baseline.error or '无'}",
                f"- B 错误：{item.langextract.error or '无'}",
                "",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
