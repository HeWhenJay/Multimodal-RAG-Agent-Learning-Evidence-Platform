from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ragas_eval_common import (
    DEFAULT_CASES_PATH,
    DEFAULT_DOCUMENTS_PATH,
    DEFAULT_OUTPUT_DIR,
    ensure_ragas_eval_config,
    ensure_output_dir,
    get_ragas_version,
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
    if args.mode == "ragas":
        ensure_ragas_eval_config()
    run_result = run_project_eval(cases_path=args.cases, documents_path=args.documents)
    write_jsonl(args.output_dir / "ragas_input.jsonl", run_result.ragas_rows)
    write_csv(args.output_dir / "offline_scores.csv", run_result.rows)
    ragas_summary = {}
    ragas_version = None

    if args.mode == "ragas":
        ragas_version = get_ragas_version()
        ragas_summary = run_ragas_metrics(run_result.ragas_rows, args.output_dir / "ragas_scores.csv")

    summary = dict(run_result.summary)
    if ragas_summary:
        summary["ragas_scores"] = ragas_summary
    write_run_config(args.output_dir / "run_config.json", mode=args.mode, summary=summary, ragas_version=ragas_version)
    write_manual_review(args.output_dir / "manual_review.md", run_result.rows, summary, ragas_version=ragas_version)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("offline_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
