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
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="评估用例 JSONL 路径")
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS_PATH, help="评估文档清单 JSON 路径")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="评估输出目录")
    return parser.parse_args()


def main() -> int:
    """运行评估并写出所有报告文件。"""
    args = parse_args()
    ensure_output_dir(args.output_dir)
    run_result = run_project_eval(cases_path=args.cases, documents_path=args.documents)
    ragas_input_path = args.output_dir / "ragas_input.jsonl"
    offline_scores_path = args.output_dir / "offline_scores.csv"
    ragas_scores_path = args.output_dir / "ragas_scores.csv"
    run_config_path = args.output_dir / "run_config.json"
    manual_review_path = args.output_dir / "manual_review.md"
    write_jsonl(ragas_input_path, run_result.ragas_rows)
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
        "offline_scores": str(offline_scores_path),
        "manual_review": str(manual_review_path),
        "run_config": str(run_config_path),
    }
    if ragas_summary:
        output_paths["ragas_scores"] = str(ragas_scores_path)
    rerun_command = "\n".join(
        [
            "$env:PYTHONPATH='ai-python'",
            "$env:RAGAS_EVAL_PROVIDER='openai-compatible'",
            "$env:RAGAS_EVAL_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'",
            "$env:DASHSCOPE_API_KEY='<your-dashscope-api-key>'",
            "$env:RAGAS_EVAL_LLM_MODEL='qwen-plus'",
            "$env:RAGAS_EVAL_EMBEDDING_MODEL='text-embedding-v4'",
            "$env:RAGAS_EVAL_TIMEOUT_SECONDS='60'",
            "$env:RAGAS_EVAL_TEMPERATURE='0'",
            f"python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas --output-dir {args.output_dir}",
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
    return 0 if summary.get("offline_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
