# Ragas 小样本 RAG 效果评估方案

更新日期：2026-06-20

## 目标

本方案用于验证当前项目的 RAG 闭环：资料入库、递归切块、Multi-Query、BM25 与向量召回、RRF/RAG-Fusion、重排、回答生成和 evidence 引用。评估数据来自本机笔记库 `C:\Users\WhenJayHe\notes\study\八股\llm相关`，首轮规模控制在人工 30 分钟内可核验。

评估不覆盖 Agent 编排、长任务调度、自主规划或工具调用。当前阶段只评估 RAG 管道本身。本轮评估集切换只更新 `docs/testing/ragas-small-evaluation-plan.md`、`docs/testing/ragas-small-eval-documents.json`、`docs/testing/ragas-small-eval-cases.jsonl` 和 `ai-python/tests/test_ragas_eval_common.py`，不修改 Java、前端、数据库脚本或生产 RAG 检索逻辑。

## 数据范围

首轮只索引 `C:\Users\WhenJayHe\notes\study\八股\llm相关` 下 10 篇 Markdown 笔记，避免把整个 Obsidian 知识库一次性放入评估，降低人工核验成本。固定文档清单位于 `docs/testing/ragas-small-eval-documents.json`，用例位于 `docs/testing/ragas-small-eval-cases.jsonl`。

| 编号 | documentId | 文件 | 覆盖能力 |
| --- | --- | --- | --- |
| D01 | `llm-ragas-d01` | `19_rag.md` | RAG 工作原理、RAG vs 微调、优势与局限 |
| D02 | `llm-ragas-d02` | `20_chunk.md` | Chunking 策略、切分粒度、overlap |
| D03 | `llm-ragas-d03` | `21_embedding.md` | Embedding、语义向量、模型选择 |
| D04 | `llm-ragas-d04` | `22_rag_optimization.md` | RAG 检索质量优化技术 |
| D05 | `llm-ragas-d05` | `23_rag_evaluate.md` | 检索/生成分阶段评估指标 |
| D06 | `llm-ragas-d06` | `26_rag_search.md` | RAG 检索召回链路与检索方式 |
| D07 | `llm-ragas-d07` | `29_rag_ops.md` | RAG 检索不到问题时的定位排查 |
| D08 | `llm-ragas-d08` | `30_ragas.md` | RAGAS 指标、流程、数据构造 |
| D09 | `llm-ragas-d09` | `33_rag_rerank.md` | Rerank 作用、落地和验证 |
| D10 | `llm-ragas-d10` | `34_rag_store.md` | RAG 存储架构、权限隔离、多级索引 |

其中 10 条 `case_type=ragas` 主样本进入自动评分，2 条 `case_type=manual_boundary` 只做人审契约检查。

## 依赖版本

已验证目标版本为 `ragas==0.4.0` 与 `ragas==0.4.3`。Python 依赖清单中保留 `ragas>=0.4,<0.5`，其它 0.4 小版本按兼容层尽力支持。

真实 Ragas 模式的指标创建集中在 `ai-python/tests/evaluation/ragas_eval_common.py` 的兼容层中。现代主路径使用 `ragas.evaluate` 或 `ragas.aevaluate`、`ragas.llms.llm_factory`、`ragas.embeddings.OpenAIEmbeddings` 和 `ragas.metrics.collections`，指标类为 `ContextPrecisionWithReference` 或 `ContextPrecision`、`ContextRecall`、`Faithfulness`、`AnswerRelevancy`。现代路径主要依赖 `ragas/openai/datasets`。

仅当现代路径无法构造时，才回退到 `langchain_openai.ChatOpenAI`、`langchain_openai.OpenAIEmbeddings`、`LangchainLLMWrapper`、`LangchainEmbeddingsWrapper` 与 `ragas.metrics` 下的 legacy 指标。`langchain-openai` 只用于 legacy fallback，不与 `ragas.metrics.collections` 混用。

离线模式不导入 Ragas，不需要评估模型 Key。

依赖安装或同步命令：

```powershell
conda activate learning-evidence-rag
conda env update -f ai-python/environment.yml --prune
```

也可以使用 pip 依赖清单：

```powershell
python -m pip install -r ai-python/requirements.txt
```

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
$env:RAGAS_EVAL_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:DASHSCOPE_API_KEY='<your-dashscope-api-key>'
$env:RAGAS_EVAL_LLM_MODEL='qwen-plus'
$env:RAGAS_EVAL_EMBEDDING_MODEL='text-embedding-v4'
$env:RAGAS_EVAL_TIMEOUT_SECONDS='60'
$env:RAGAS_EVAL_TEMPERATURE='0'
python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas
```

真实 Ragas 评分默认复用项目百炼配置。`RAGAS_EVAL_API_KEY` 优先级高于 `DASHSCOPE_API_KEY`；未配置 `RAGAS_EVAL_API_KEY` 时会直接读取 `DASHSCOPE_API_KEY`。`RAGAS_EVAL_LLM_MODEL` 和 `RAGAS_EVAL_EMBEDDING_MODEL` 未配置时，会分别回退到 `RAG_LLM_MODEL`、`RAG_EMBEDDING_MODEL` 或默认的 `qwen-plus`、`text-embedding-v4`。如果 Key 仍缺失，会先写出离线评估文件，再以非 0 返回真实评分失败原因。

真实评分环境变量规则：

| 环境变量 | 必填 | 规则 |
| --- | --- | --- |
| `RAGAS_EVAL_PROVIDER` | 是 | 只允许 `openai-compatible` 或 `openai`；传给 Ragas 内部时统一映射为 `openai` |
| `RAGAS_EVAL_BASE_URL` | openai-compatible 可选 | 必须以 `http://` 或 `https://` 开头；未配置时依次复用 `RAG_LLM_BASE_URL`、`RAG_EMBEDDING_BASE_URL`、`DASHSCOPE_EMBEDDING_BASE_URL`，再回退到百炼兼容地址 |
| `RAGAS_EVAL_API_KEY` | 可选 | 评估模型 Key，不写入输出文件；未配置时复用 `DASHSCOPE_API_KEY` |
| `DASHSCOPE_API_KEY` | `RAGAS_EVAL_API_KEY` 为空时必填 | 项目百炼 Key，可直接用于真实 Ragas 评分 |
| `RAGAS_EVAL_LLM_MODEL` | 可选 | Ragas 评估 LLM 模型；未配置时复用 `RAG_LLM_MODEL`，再回退到 `qwen-plus` |
| `RAGAS_EVAL_EMBEDDING_MODEL` | 可选 | Ragas 评估 embedding 模型；未配置时复用 `RAG_EMBEDDING_MODEL` 或 `DASHSCOPE_EMBEDDING_MODEL`，再回退到 `text-embedding-v4` |
| `RAGAS_EVAL_TIMEOUT_SECONDS` | 是 | 必须是数字且大于 0，会传给 OpenAI client、embedding 和 Ragas `RunConfig` |
| `RAGAS_EVAL_TEMPERATURE` | 是 | 必须是数字且 `0 <= x <= 2`，会传给评估 LLM |

## 输出物

每次评估输出到 `tmp/ragas-small-eval/`，该目录不提交。

| 文件 | 内容 |
| --- | --- |
| `ragas_input.jsonl` | Ragas 实际输入，包含问题、回答、上下文、参考答案和项目辅助字段 |
| `offline_scores.csv` | 离线文档级命中、引用结构、边界契约和关键点覆盖结果 |
| `ragas_scores.csv` | 真实 Ragas LLM 指标结果，仅 `--mode ragas` 生成 |
| `manual_review.md` | 人工 5 分制复核入口、失败原因和下一步建议 |
| `run_config.json` | 本次 RAG 配置、Ragas 版本、评估模型和汇总结果 |

`--mode ragas` 会先创建输出目录、运行项目离线评估、写出 `ragas_input.jsonl` 和 `offline_scores.csv`，再校验 Ragas 配置并运行真实评分。真实评分失败时不会生成假的 `ragas_scores.csv`，但仍会写出 `manual_review.md` 与 `run_config.json`，其中包含 `ragas.failureReason` 和 `summary.ragas_failure_reason`，方便补齐配置后复跑。

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
python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas --output-dir tmp/ragas-small-eval-missing-config
```

第三条在未配置真实评估 Key 时预期返回非 0，但必须已经写出离线输出、`run_config.json` 和失败原因。

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
