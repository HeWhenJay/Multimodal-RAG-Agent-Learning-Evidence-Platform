# Ragas 小样本 RAG 效果评估方案

更新日期：2026-06-19

## 目标

本方案用于验证当前项目的 RAG 闭环：资料入库、递归切块、Multi-Query、BM25 与向量召回、RRF/RAG-Fusion、重排、回答生成和 evidence 引用。评估数据来自本机笔记库 `C:\Users\WhenJayHe\notes\study`，首轮规模控制在人工 30 分钟内可核验。

评估不覆盖 Agent 编排、长任务调度、自主规划或工具调用。当前阶段只评估 RAG 管道本身。实现范围只在 `ai-python/tests/evaluation/`、`ai-python/tests/test_ragas_eval_common.py`、`docs/testing/` 和 Python 依赖清单内，不修改 Java、前端、数据库脚本或生产 RAG 检索逻辑。

## 数据范围

首轮只索引 10 篇 Markdown 笔记，避免把整个 Obsidian 知识库一次性放入评估，降低人工核验成本。固定文档清单位于 `docs/testing/ragas-small-eval-documents.json`，用例位于 `docs/testing/ragas-small-eval-cases.jsonl`。

| 编号 | documentId | 文件 | 覆盖能力 |
| --- | --- | --- | --- |
| D01 | `ragas-d01` | `RAG效果评估量化.md` | 评估必要性、评估依据 |
| D02 | `ragas-d02` | `RAG检索评估.md` | Context Precision、Context Recall |
| D03 | `ragas-d03` | `RAG响应评估.md` | Faithfulness、Response Relevancy |
| D04 | `ragas-d04` | `RAG常用评估工具.md` | Ragas 使用范围和字段 |
| D05 | `ragas-d05` | `Multi-Query多路召回-痛点分析.md` | Multi-Query 查询改写 |
| D06 | `ragas-d06` | `RAG-Fusion-痛点分析.md` | RAG-Fusion、RRF |
| D07 | `ragas-d07` | `RAG中检索优化.md` | 混合检索 |
| D08 | `ragas-d08` | `元数据过滤-痛点分析.md` | 元数据过滤 |
| D09 | `ragas-d09` | `摘要索引-痛点分析.md` | 摘要索引 |
| D10 | `ragas-d10` | `上下文压缩和过滤-痛点分析.md` | 后检索压缩和过滤，作为干扰与扩展材料 |

其中 10 条 `case_type=ragas` 主样本进入自动评分，2 条 `case_type=manual_boundary` 只做人审契约检查。

## 依赖版本

Python 依赖清单中使用 `ragas>=0.4,<0.5`。真实 Ragas 模式的指标创建集中在 `ai-python/tests/evaluation/ragas_eval_common.py` 的兼容层中，优先尝试 `ragas.metrics.collections`，失败时回退到 `ragas.metrics` 下的等价指标类。

离线模式不导入 Ragas，不需要评估模型 Key。

## 执行边界

评估脚本必须在导入 `app.main` 或 `app.api.rag` 前设置以下环境变量，因为 Python RAG 的 `store` 是模块级单例：

```powershell
$env:RAG_STORE_BACKEND='memory'
$env:RAG_EMBEDDING_PROVIDER='hash'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAG_ANSWER_PROVIDER='local'
$env:RAG_RERANK_PROVIDER='local'
```

脚本使用 FastAPI `TestClient` 直接调用 Python 内部接口，不启动 Java、前端或真实 `uvicorn`。索引使用 `/internal/rag/documents/index-text`，查询使用 `/internal/rag/query`。

样本文件保留 `top_k`、`metadata_filter` 等便于阅读的 snake_case 字段，适配层会转换为项目接口需要的 `topK`、`metadataFilter`。

## Ragas 数据映射

从项目 `/internal/rag/query` 响应映射到 Ragas 单轮评估样本：

| 项目字段 | Ragas 字段 | 说明 |
| --- | --- | --- |
| `question` | `user_input` | 用户原始问题 |
| `answer` | `response` | RAG 生成回答 |
| `evidences[].snippet` | `retrieved_contexts` | 用于评估的检索上下文，保持原排序 |
| `reference` | `reference` | 人工参考答案 |

导出的 `ragas_input.jsonl` 还会保留项目辅助字段：`case_id`、`retrieved_context_ids`、`retrieved_document_ids`、`expected_document_ids`。这些字段用于人工核验和离线文档级命中评估，不依赖 Ragas 内置 schema 接收。

## 指标选择

离线模式只运行项目自身可确定的结构检查：

| 指标 | 说明 |
| --- | --- |
| 文档级 top1/top3 命中 | 用 `expected_document_ids` 对比返回 evidence 的 `documentId` |
| 关键点覆盖率 | 用 `expected_answer_points` 与回答文本做粗粒度包含检查 |
| evidence 引用结构 | 检查回答是否保留可追踪 evidence 引用，且 evidence 有标题、章节、来源和分数 |
| 边界样本契约 | 检查无关问题和不存在 `documentType` 过滤不应返回有效证据 |

真实 Ragas 模式在离线检查基础上增加 4 个 LLM 指标：

| 指标 | 关注点 | 失败时优先排查 |
| --- | --- | --- |
| Context Precision | 相关上下文是否排在前面 | BM25、向量召回、RRF、rerank |
| Context Recall | 回答所需关键证据是否被召回 | Multi-Query、切块粒度、metadata 过滤 |
| Faithfulness | 回答是否基于 evidence，没有编造 | Prompt、回答生成模型、引用约束 |
| Response Relevancy | 回答是否直接回应问题 | Query 改写、上下文噪声、回答 Prompt |

## 运行命令

离线快速验收：

```powershell
$env:PYTHONPATH='ai-python'
python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode offline
```

真实 Ragas 评分：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAGAS_EVAL_PROVIDER='openai-compatible'
$env:RAGAS_EVAL_BASE_URL='https://your-openai-compatible-endpoint/v1'
$env:RAGAS_EVAL_API_KEY='<your-eval-key>'
$env:RAGAS_EVAL_LLM_MODEL='<your-eval-llm>'
$env:RAGAS_EVAL_EMBEDDING_MODEL='<your-eval-embedding>'
$env:RAGAS_EVAL_TIMEOUT_SECONDS='60'
$env:RAGAS_EVAL_TEMPERATURE='0'
python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas
```

评估模型配置与项目 RAG 模型配置分离。`--mode ragas` 缺少 `RAGAS_EVAL_API_KEY`、`RAGAS_EVAL_LLM_MODEL` 或 `RAGAS_EVAL_EMBEDDING_MODEL` 时会直接报错，并提示改用 `--mode offline`，不会静默复用项目的 `DASHSCOPE_API_KEY`。

## 输出物

每次评估输出到 `tmp/ragas-small-eval/`，该目录不提交。

| 文件 | 内容 |
| --- | --- |
| `ragas_input.jsonl` | Ragas 实际输入，包含问题、回答、上下文、参考答案和项目辅助字段 |
| `offline_scores.csv` | 离线文档级命中、引用结构、边界契约和关键点覆盖结果 |
| `ragas_scores.csv` | 真实 Ragas LLM 指标结果，仅 `--mode ragas` 生成 |
| `manual_review.md` | 人工 5 分制复核入口、失败原因和下一步建议 |
| `run_config.json` | 本次 RAG 配置、Ragas 版本、评估模型和汇总结果 |

## 通过门槛

离线模式必须通过：

| 项 | 门槛 |
| --- | --- |
| 主样本文档级 top3 命中 | `>= 9 / 10` |
| 主样本引用结构合格 | `10 / 10` |
| 边界样本 | `2 / 2` |
| 主样本 evidence 为空 | `<= 2` |

真实 Ragas 模式建议门槛：

| 项 | 门槛 |
| --- | --- |
| Faithfulness 平均值 | `>= 0.85` |
| Response Relevancy 平均值 | `>= 0.80` |
| Context Precision 平均值 | `>= 0.70` |
| Context Recall 平均值 | `>= 0.75` |

阻断问题：回答引用了未返回的 evidenceId；跨 `metadataFilter` 返回其它用户或其它过滤范围资料；无 evidence 时编造确定答案；超过 2 条主样本 evidence 为空。

## 验证命令

本任务的最小验证：

```powershell
$env:PYTHONPATH='ai-python'
python -B -m pytest ai-python/tests/test_ragas_eval_common.py -q
python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode offline
```

如评估工具或测试依赖影响面扩大，再运行：

```powershell
$env:PYTHONPATH='ai-python'
python -B -m pytest ai-python/tests -q
```

不要求运行 Java 或前端验证，因为本评估工具只新增 Python 测试工具和文档。

## 官方参考

- Ragas Evaluate API：`https://docs.ragas.io/en/stable/references/evaluate/`
- Ragas Evaluation Schema：`https://docs.ragas.io/en/stable/references/evaluation_schema/`
- Ragas Metrics：`https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/`
