from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from ragas_eval_common import (
        DEFAULT_CASES_PATH,
        DEFAULT_DOCUMENTS_PATH,
        DEFAULT_OUTPUT_DIR,
        ensure_output_dir,
        load_ragas_eval_settings,
        run_project_eval,
        run_project_ragas_input,
        run_ragas_case_by_case,
        run_ragas_metrics,
        write_csv,
        write_jsonl,
        write_manual_review,
        write_run_config,
    )
except ModuleNotFoundError:
    from .ragas_eval_common import (
        DEFAULT_CASES_PATH,
        DEFAULT_DOCUMENTS_PATH,
        DEFAULT_OUTPUT_DIR,
        ensure_output_dir,
        load_ragas_eval_settings,
        run_project_eval,
        run_project_ragas_input,
        run_ragas_case_by_case,
        run_ragas_metrics,
        write_csv,
        write_jsonl,
        write_manual_review,
        write_run_config,
    )


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="运行 Ragas 小样本 RAG 效果评估")
    parser.add_argument("--mode", choices=["offline", "ragas"], default="offline", help="offline 只跑确定性检查，ragas 额外跑 LLM 指标")
    parser.add_argument(
        "--rag-profile",
        choices=["current"],
        default="current",
        help="current 使用生产同款 RAG 全流程，并在同一个 PostgreSQL 数据库中使用 Ragas_Test 前缀表隔离",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="仅 --mode ragas 可用：复用已存在的 Ragas_Test 索引，只执行真实查询和 Ragas 指标",
    )
    parser.add_argument(
        "--no-case-by-case",
        action="store_true",
        help="关闭逐条模式：一次性批量运行全部用例的 RAG 查询和 Ragas 评分（默认逐条）",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="评估用例 JSONL 路径")
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS_PATH, help="评估文档清单 JSON 路径")
    parser.add_argument("--case-id", default=None, help="只运行指定 case_id 的评估用例，例如 R01")
    parser.add_argument("--case-index", type=int, default=None, help="只运行第 N 条候选评估用例，1 基序号；ragas 模式只在主样本内计数")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="评估输出目录")
    return parser.parse_args()


def main() -> int:
    """运行评估并写出所有报告文件。"""
    args = parse_args()
    if args.skip_index and args.mode != "ragas":
        print("--skip-index 只允许配合 --mode ragas 使用。", file=sys.stderr)
        return 2
    ensure_output_dir(args.output_dir)
    has_case_filter = args.case_id is not None or args.case_index is not None
    case_by_case = args.mode == "ragas" and not has_case_filter and not getattr(args, "no_case_by_case", True)

    if case_by_case:
        try:
            run_config_path = args.output_dir / "run_config.json"
            summary = run_ragas_case_by_case(
                cases_path=args.cases,
                documents_path=args.documents,
                rag_profile=args.rag_profile,
                index_documents=not args.skip_index,
                output_dir=args.output_dir,
            )
        except Exception as exc:
            print(f"逐条评估失败：{exc}", file=sys.stderr)
            return 1
        write_run_config(
            run_config_path,
            mode=args.mode,
            summary=summary,
            output_paths={"ragas_scores_summary": str(args.output_dir / "ragas_scores_summary.csv")},
            ragas_version=None,
            ragas_settings=None,
            ragas_model_adapter=None,
            metric_names=[],
            failure_reason=None,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    try:
        if args.mode == "ragas":
            run_result = run_project_ragas_input(
                cases_path=args.cases,
                documents_path=args.documents,
                rag_profile=args.rag_profile,
                index_documents=not args.skip_index,
                case_id=args.case_id,
                case_index=args.case_index,
            )
        else:
            run_result = run_project_eval(
                cases_path=args.cases,
                documents_path=args.documents,
                rag_profile=args.rag_profile,
                case_id=args.case_id,
                case_index=args.case_index,
            )
    except ValueError as exc:
        print(f"评估用例参数错误：{exc}", file=sys.stderr)
        return 2
    ragas_input_path = args.output_dir / "ragas_input.jsonl"
    offline_scores_path = args.output_dir / "offline_scores.csv"
    ragas_scores_path = args.output_dir / "ragas_scores.csv"
    run_config_path = args.output_dir / "run_config.json"
    manual_review_path = args.output_dir / "manual_review.md"
    write_jsonl(ragas_input_path, run_result.ragas_rows)
    if args.mode == "offline":
        write_csv(offline_scores_path, run_result.rows)
    ragas_summary = {}
    ragas_version = None
    ragas_settings = None
    ragas_model_adapter = None
    ragas_metric_names: list[str] = []
    ragas_failure_reason = None

    if args.mode == "ragas":
        if ragas_scores_path.exists():
            ragas_scores_path.unlink()
        try:
            ragas_settings = load_ragas_eval_settings()
            ragas_result = run_ragas_metrics(run_result.ragas_rows, ragas_scores_path, settings=ragas_settings)
            ragas_summary = ragas_result.summary
            ragas_version = ragas_result.ragas_version
            ragas_model_adapter = ragas_result.model_adapter
            ragas_metric_names = ragas_result.metric_names
        except Exception as exc:
            ragas_failure_reason = str(exc)

    summary = dict(run_result.summary)
    if ragas_summary:
        summary["ragas_scores"] = ragas_summary
    if ragas_failure_reason:
        summary["ragas_failure_reason"] = ragas_failure_reason
    output_paths = {
        "ragas_input": str(ragas_input_path),
        "run_config": str(run_config_path),
    }
    if args.mode == "offline":
        output_paths["offline_scores"] = str(offline_scores_path)
        output_paths["manual_review"] = str(manual_review_path)
    if ragas_summary:
        output_paths["ragas_scores"] = str(ragas_scores_path)
    rerun_command = "\n".join(
        [
            "$env:PYTHONPATH='ai-python'",
            "$env:RAGAS_TEST_TABLE_PREFIX='Ragas_Test_'",
            "$env:RAGAS_EVAL_PROVIDER='openai-compatible'",
            "$env:RAGAS_EVAL_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'",
            "$env:DASHSCOPE_API_KEY='<your-dashscope-api-key>'",
            "$env:RAGAS_EVAL_LLM_MODEL='qwen-plus'",
            "$env:RAGAS_EVAL_EMBEDDING_MODEL='text-embedding-v4'",
            "$env:RAGAS_EVAL_TIMEOUT_SECONDS='60'",
            "$env:RAGAS_EVAL_MAX_RETRIES='2'",
            "$env:RAGAS_EVAL_MAX_WAIT_SECONDS='10'",
            "$env:RAGAS_EVAL_MAX_WORKERS='2'",
            "$env:RAGAS_EVAL_BATCH_SIZE='1'",
            "$env:RAGAS_EVAL_TEMPERATURE='0'",
            "$env:RAGAS_EVAL_MAX_TOKENS='4096'",
            f"python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode ragas --rag-profile {args.rag_profile} --output-dir {args.output_dir}",
        ]
    )
    write_run_config(
        run_config_path,
        mode=args.mode,
        summary=summary,
        output_paths=output_paths,
        ragas_version=ragas_version,
        ragas_settings=ragas_settings,
        ragas_model_adapter=ragas_model_adapter,
        metric_names=ragas_metric_names,
        failure_reason=ragas_failure_reason,
    )
    if args.mode == "offline":
        write_manual_review(
            manual_review_path,
            run_result.rows,
            summary,
            ragas_version=ragas_version,
            ragas_failure_reason=ragas_failure_reason,
            ragas_metric_names=ragas_metric_names,
            rerun_command=rerun_command,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if ragas_failure_reason:
        print(f"真实 Ragas 评分失败：{ragas_failure_reason}", file=sys.stderr)
        return 1
    if args.mode == "ragas":
        return 0
    return 0 if summary.get("offline_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
