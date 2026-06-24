# Ragas 小样本 RAG 效果评估方案

更新日期：2026-06-24

## 目标

本方案用于验证当前项目的 RAG 闭环：资料入库、递归切块、Multi-Query、BM25 与向量召回、RRF/RAG-Fusion、重排、回答生成和 evidence 引用。评估数据来自本机笔记库 `C:\Users\WhenJayHe\notes\study\八股\llm相关`，首轮规模控制在人工 30 分钟内可核验。

评估不覆盖 Agent 编排、长任务调度、自主规划或工具调用。当前阶段只评估 RAG 管道本身。本轮评估集切换只更新评估数据、评估脚本和 Python 侧测试隔离能力，不修改 Java、前端或生产 RAG 检索算法。

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

其中 10 条 `case_type=ragas` 主样本进入自动评分，5 条 `case_type=manual_boundary` 只做人审契约检查。边界样本优先使用结构化 `answerStatus/refusalReason` 判定，文案拒答匹配仅作为旧响应兼容。

## 依赖版本

已验证目标版本为 `ragas==0.4.0` 与 `ragas==0.4.3`。Python 依赖清单中保留 `ragas>=0.4,<0.5`，其它 0.4 小版本按兼容层尽力支持。

真实 Ragas 模式的指标创建集中在 `ai-python/tests/evaluation/ragas_eval_common.py` 的兼容层中。现代主路径使用 `ragas.evaluate` 或 `ragas.aevaluate`、`ragas.llms.llm_factory`、`ragas.embeddings.OpenAIEmbeddings` 和 `ragas.metrics`，指标类为 `LLMContextPrecisionWithReference`、`LLMContextRecall`、`Faithfulness`、`AnswerRelevancy`。现代路径主要依赖 `ragas/openai/datasets`。

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

评估脚本只允许真实项目 RAG 运行档位：

| 档位 | 参数 | 用途 |
| --- | --- | --- |
| 当前项目链路 | `--rag-profile current` | 默认且唯一档位。加载 `ai-python/config/application.yml`，并强制使用生产同款 PostgreSQL/pgvector、DashScope `text-embedding-v4`、`qwen3-rerank` 和 `qwen-plus` |

脚本使用 FastAPI `TestClient` 直接调用 Python 内部接口，不启动 Java、前端或真实 `uvicorn`。索引使用 `/internal/rag/documents/index-text`，查询使用 `/internal/rag/query`。

`current` 档位在导入 FastAPI app 前完成测试隔离配置：

| 项 | 生产 | Ragas 测评 current 档 |
| --- | --- | --- |
| Embedding | DashScope `text-embedding-v4` | 完全一致 |
| 回答生成 | DashScope/OpenAI-compatible `qwen-plus` | 完全一致 |
| 重排 | DashScope `qwen3-rerank` | 完全一致 |
| 存储引擎 | PostgreSQL + pgvector | 完全一致 |
| 数据库 | `RAG_DATABASE_URL` 指向的生产同款数据库 | 完全一致，不派生新数据库 |
| 表名 | `rag_document`、`rag_chunk` | `"Ragas_Test_rag_document"`、`"Ragas_Test_rag_chunk"` |

Ragas 测评数据必须写入同一个 PostgreSQL 数据库中带 `Ragas_Test` 前缀的对象。默认表名前缀为 `Ragas_Test_`，可用 `RAGAS_TEST_TABLE_PREFIX` 覆盖，但覆盖值也必须以 `Ragas_Test` 开头。对应干净初始化 SQL 位于 `infra/sql/init.sql`，增量迁移位于 `infra/sql/alter-database/20260621_0100_create_ragas_test_pgvector_store.sql`。Ragas 只消费项目 RAG 输出的 `ragas_input.jsonl` 做核验，不参与索引、召回、重排或回答生成。

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
| 边界样本契约 | 检查无关问题、不存在 `documentType` 过滤、低相关误召回、弱 snippet 和 summary child 候选不应返回顶层有效证据 |

边界样本判定规则：

- 新响应优先读取 `answerStatus/refusalReason`。`answerStatus=REFUSED` 且 `evidences=[]` 视为通过，再核对 `refusalReason` 是否落在样本期望范围。
- 旧响应缺少 `answerStatus` 时，继续按 evidence 数量、最高分和拒答文案做兼容判断。
- `REFUSED` 时弱候选只能出现在 `diagnostics.answerGuard.candidateEvidenceSummaries`，不得出现在顶层 `evidences`。
- `B02` 仍额外校验 metadataFilter 不泄漏其它 `documentType` 文档。

真实 Ragas 模式不运行离线命中率、关键点覆盖率或边界样本门槛，只使用生产同款 RAG 输出的回答与上下文执行 4 个 LLM 指标：

| 指标 | 关注点 | 失败时优先排查 |
| --- | --- | --- |
| Context Precision | 相关上下文是否排在前面 | BM25、向量召回、RRF、rerank |
| Context Recall | 回答所需关键证据是否被召回 | Multi-Query、切块粒度、metadata 过滤 |
| Faithfulness | 回答是否基于 evidence，没有编造 | Prompt、回答生成模型、引用约束 |
| Response Relevancy | 回答是否直接回应问题 | Query 改写、上下文噪声、回答 Prompt |

真实 Ragas 默认运行上述 4 个指标。若评估模型调用耗时过长，可用 `RAGAS_EVAL_METRICS` 只运行指标子集，例如 `context_recall` 或 `context_precision,context_recall`。这只影响 Ragas 核验阶段，不改变项目 RAG 索引、召回、重排和回答链路。

如果 `Ragas_Test` 表中已经存在完整的 10 篇评估资料索引，可在 `--mode ragas` 下追加 `--skip-index`，只复用现有索引执行生产同款 RAG 查询和真实 Ragas 指标，避免重复生成 embedding 和重写评估表。该参数只允许用于真实 Ragas 模式，不运行离线命中率、关键点覆盖率或边界样本门槛。

排查 `faithfulness`、`answer_relevancy` 等慢指标时，可追加 `--case-id R01` 或 `--case-index 1` 只运行单条评估用例。`--case-index` 是 1 基序号；在 `--mode ragas` 下只按 `case_type=ragas` 的主样本计数，不包含边界样本。`--case-id` 与 `--case-index` 只能二选一。

## 运行命令

离线快速验收：

```powershell
$env:PYTHONPATH='ai-python'
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode offline
```

当前项目 RAG 全流程离线验收：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAGAS_TEST_TABLE_PREFIX='Ragas_Test_'
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode offline --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-offline
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
$env:RAGAS_EVAL_MAX_RETRIES='2'
$env:RAGAS_EVAL_MAX_WAIT_SECONDS='10'
$env:RAGAS_EVAL_MAX_WORKERS='2'
$env:RAGAS_EVAL_BATCH_SIZE='1'
$env:RAGAS_EVAL_TEMPERATURE='0'
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas
```

当前项目 RAG 全流程加 Ragas 核验：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAGAS_TEST_TABLE_PREFIX='Ragas_Test_'
$env:RAGAS_EVAL_PROVIDER='openai-compatible'
$env:RAGAS_EVAL_TIMEOUT_SECONDS='300'
$env:RAGAS_EVAL_MAX_RETRIES='1'
$env:RAGAS_EVAL_MAX_WAIT_SECONDS='10'
$env:RAGAS_EVAL_MAX_WORKERS='1'
$env:RAGAS_EVAL_BATCH_SIZE='1'
$env:RAGAS_EVAL_TEMPERATURE='0'
$env:RAGAS_EVAL_METRICS='context_recall'
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-ragas
```

复用已存在 `Ragas_Test` 索引，只跑真实生产查询和 Ragas 指标：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAGAS_TEST_TABLE_PREFIX='Ragas_Test_'
$env:RAGAS_EVAL_PROVIDER='openai-compatible'
$env:RAGAS_EVAL_TIMEOUT_SECONDS='300'
$env:RAGAS_EVAL_MAX_RETRIES='1'
$env:RAGAS_EVAL_MAX_WAIT_SECONDS='10'
$env:RAGAS_EVAL_MAX_WORKERS='1'
$env:RAGAS_EVAL_BATCH_SIZE='1'
$env:RAGAS_EVAL_TEMPERATURE='0'
$env:RAGAS_EVAL_METRICS='answer_relevancy'
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas --skip-index --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-ragas-reuse-index
```

逐条排查真实 Ragas 慢指标：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAGAS_TEST_TABLE_PREFIX='Ragas_Test_'
$env:RAGAS_EVAL_PROVIDER='openai-compatible'
$env:RAGAS_EVAL_TIMEOUT_SECONDS='60'
$env:RAGAS_EVAL_MAX_RETRIES='0'
$env:RAGAS_EVAL_MAX_WAIT_SECONDS='5'
$env:RAGAS_EVAL_MAX_WORKERS='1'
$env:RAGAS_EVAL_BATCH_SIZE='1'
$env:RAGAS_EVAL_TEMPERATURE='0'
$env:RAGAS_EVAL_METRICS='faithfulness'
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas --skip-index --rag-profile current --case-index 1 --output-dir tmp/ragas-single-case-faithfulness-r01
```

PyCharm 的 Parameters 可直接使用：

```text
--mode ragas --skip-index --rag-profile current --case-index 1 --output-dir tmp/ragas-single-case-faithfulness-r01
```

如果要按样本编号运行，可改用：

```text
--mode ragas --skip-index --rag-profile current --case-id R01 --output-dir tmp/ragas-single-case-faithfulness-r01
```

真实 Ragas 评分默认复用项目百炼配置。`RAGAS_EVAL_API_KEY` 优先级高于 `DASHSCOPE_API_KEY`；未配置 `RAGAS_EVAL_API_KEY` 时会直接读取 `DASHSCOPE_API_KEY`。`RAGAS_EVAL_LLM_MODEL` 和 `RAGAS_EVAL_EMBEDDING_MODEL` 未配置时，会分别回退到 `RAG_LLM_MODEL`、`RAG_EMBEDDING_MODEL` 或默认的 `qwen-plus`、`text-embedding-v4`。如果 Key 仍缺失，`--mode ragas` 会先写出 `ragas_input.jsonl` 和 `run_config.json`，再以非 0 返回真实评分失败原因。

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
| `RAGAS_EVAL_MAX_RETRIES` | 可选 | Ragas `RunConfig.max_retries`，默认 `2`；百炼兼容接口卡住或限流时建议设为 `1` |
| `RAGAS_EVAL_MAX_WAIT_SECONDS` | 可选 | Ragas `RunConfig.max_wait`，默认 `10`，避免失败后长时间指数退避 |
| `RAGAS_EVAL_MAX_WORKERS` | 可选 | Ragas `RunConfig.max_workers`，默认 `2`；真实四指标评分卡住时建议设为 `1` |
| `RAGAS_EVAL_BATCH_SIZE` | 可选 | 传给 `ragas.evaluate(batch_size=...)`，建议设为 `1`，降低评估模型并发和排队 |
| `RAGAS_EVAL_TEMPERATURE` | 是 | 必须是数字且 `0 <= x <= 2`，会传给评估 LLM |
| `RAGAS_EVAL_METRICS` | 可选 | 逗号分隔指标子集；允许 `context_precision`、`context_recall`、`faithfulness`、`answer_relevancy`，未配置时运行全部 4 项 |
| `RAGAS_TEST_TABLE_PREFIX` | 可选 | 测评表名前缀，默认 `Ragas_Test_`，必须以 `Ragas_Test` 开头；实际表为 `"Ragas_Test_rag_document"` 和 `"Ragas_Test_rag_chunk"` |

## 输出物

每次评估输出到 `tmp/ragas-small-eval/`，该目录不提交。

| 文件 | 内容 |
| --- | --- |
| `ragas_input.jsonl` | Ragas 实际输入，包含问题、回答、上下文、参考答案和项目辅助字段 |
| `offline_scores.csv` | 仅 `--mode offline` 生成，包含离线文档级命中、引用结构、边界契约和关键点覆盖结果 |
| `ragas_scores.csv` | 真实 Ragas LLM 指标结果，仅 `--mode ragas` 生成 |
| `manual_review.md` | 仅 `--mode offline` 生成，作为人工复核入口、失败原因和下一步建议 |
| `run_config.json` | 本次 RAG 配置、Ragas 版本、评估模型和汇总结果 |

`--mode ragas` 会先创建输出目录，索引 10 篇评估资料，调用生产同款 RAG 查询 10 条主样本并写出 `ragas_input.jsonl`，随后校验 Ragas 配置并运行真实评分。追加 `--skip-index` 时会复用已有 `Ragas_Test` 索引，不重复索引资料。真实评分失败时不会生成假的 `ragas_scores.csv`，但仍会写出 `run_config.json`，其中包含 `ragas.failureReason` 和 `summary.ragas_failure_reason`，方便补齐配置后复跑。

## 通过门槛

离线模式必须通过：

| 项 | 门槛 |
| --- | --- |
| 主样本文档级 top3 命中 | `>= 9 / 10` |
| 主样本引用结构合格 | `10 / 10` |
| 边界样本 | `5 / 5` |
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
conda run -n learning-evidence-rag python -B -m pytest ai-python/tests/test_ragas_eval_common.py -q
conda run -n learning-evidence-rag python -B -m pytest ai-python/tests/test_pgvector_store.py -q
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode offline --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-offline
conda run -n learning-evidence-rag python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas --output-dir tmp/ragas-small-eval-missing-config
```

最后一条在未配置真实评估 Key 时预期返回非 0，但必须已经写出 `ragas_input.jsonl`、`run_config.json` 和失败原因，不要求生成离线指标文件。

如评估工具或测试依赖影响面扩大，再运行：

```powershell
$env:PYTHONPATH='ai-python'
conda run -n learning-evidence-rag python -B -m pytest ai-python/tests -q
```

不要求运行 Java 或前端验证，因为本评估工具只新增 Python 测试工具和文档。

## 官方参考

- Ragas Evaluate API：`https://docs.ragas.io/en/stable/references/evaluate/`
- Ragas Evaluation Schema：`https://docs.ragas.io/en/stable/references/evaluation_schema/`
- Ragas Metrics：`https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/`
