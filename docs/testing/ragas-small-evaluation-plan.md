# Ragas 小样本 RAG 效果评估方案

更新日期：2026-07-18

## 目标

本方案用于验证当前项目的 RAG 闭环：资料入库、递归切块、Multi-Query、BM25 与向量召回、RRF/RAG-Fusion、重排、回答生成和 evidence 引用。评估数据来自本机笔记库 `C:\Users\WhenJayHe\notes\study\八股\llm相关`，首轮规模控制在人工 30 分钟内可核验。

评估不覆盖 Agent 编排、长任务调度、自主规划或工具调用。当前阶段只评估 RAG 管道本身。2026-07-18 的缺口修复同时调整了确定性融合、本地降级重排和回答准入阈值，因此必须使用 `current` 档真实复验，不能只依赖评估器单元测试。

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

真实 Ragas 模式的指标创建集中在 `ai-python/rag/evaluation/ragas_eval_common.py` 的兼容层中。现代主路径使用 `ragas.evaluate` 或 `ragas.aevaluate`、`ragas.llms.llm_factory`、`ragas.embeddings.OpenAIEmbeddings` 和 `ragas.metrics`，指标类为 `LLMContextPrecisionWithReference`、`LLMContextRecall`、`Faithfulness`、`AnswerRelevancy`。现代路径主要依赖 `ragas/openai/datasets`。

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

脚本使用独立的 FastAPI `TestClient` 评估应用直接调用历史评估路由，不启动 Java、前端或真实 `uvicorn`；这些 `/internal/rag/*` 路径不会注册到生产 `app.main`，也不是公开运行契约。索引使用 `/internal/rag/documents/index-text`，查询使用 `/internal/rag/query`。

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

导出的 `ragas_input.jsonl` 还会保留项目辅助字段：`case_id`、`retrieved_context_ids`、`retrieved_document_ids`、`expected_document_ids`。其中 `expected_document_ids` 作为文档级二元 qrels，`retrieved_document_ids` 按 evidence 首次出现顺序去重；这些字段用于人工核验和离线检索排序评估，不依赖 Ragas 内置 schema 接收。

## 指标选择

离线模式只运行项目自身可确定的结构检查：

| 指标 | 说明 |
| --- | --- |
| 文档级 top1/top3 命中 | 用 `expected_document_ids` 对比返回 evidence 的 `documentId` |
| 文档级 MRR | 取首个相关文档排名的倒数，再对具有 qrels 的主样本求平均 |
| 文档级 Recall@1/@3/@5 | 计算 Top-K 中已召回期望文档数占全部期望文档数的比例 |
| 文档级 NDCG@5 | 按 `expected_document_ids` 的二元相关性计算折损累计增益，衡量相关文档是否排在前面 |
| 关键点覆盖率 | 用 `expected_answer_points` 与回答文本做粗粒度包含检查 |
| evidence 引用结构 | 检查回答是否保留可追踪 evidence 引用，且 evidence 有标题、章节、来源和分数 |
| 边界样本契约 | 检查无关问题、不存在 `documentType` 过滤、低相关误召回、弱 snippet 和 summary child 候选不应返回顶层有效证据 |

边界样本判定规则：

- 每条边界样本显式声明 `expected_answer_status`、`expected_refusal_reasons`、`max_evidence_count` 和 `require_no_unexpected_documents`，评估器不再按 `case_id` 硬编码特例。
- 当前 5 条边界样本都要求 `answerStatus=REFUSED`、`evidences=[]`，并核对 `refusalReason` 是否落在样本允许范围。回答正文即使出现“无法回答”“证据不足”等措辞，也不能覆盖结构化 `ANSWERED` 状态或顶层 evidence 泄漏。
- 旧响应只有在样本未设置 `requires_structured_response=true` 时，才按 evidence 数量、最高分和拒答文案走兼容判定。
- `REFUSED` 时弱候选只能出现在 `diagnostics.answerGuard.candidateEvidenceSummaries`，不得出现在顶层 `evidences`。
- `B02` 要求 `FILTERED_OUT`；`B04/B05` 的目标过滤范围当前可能没有匹配 chunk，因此 `FILTERED_OUT` 是比 `WEAK_SNIPPET` 或 `ONLY_DIAGNOSTIC_CANDIDATES` 更早、也更保守的合法拒答分支。

### 2026-07-18 边界结果复盘

`tmp/ragas-refusal-eval` 中记录的“边界样本通过 3 / 5”混合了一个误放行和两个误拒绝，不能解释为只有 `B04/B05` 存在产品缺陷：

- `B03` 返回了 5 条与烘焙问题无关的 RAG evidence，并调用回答模型生成了带引用的正文。旧评估器仅因正文包含“无法提供”“缺少”等拒答片段而判为通过，掩盖了顶层 evidence 泄漏。
- `B04/B05` 都返回“当前筛选条件下没有可用证据”，该正文由 `FILTERED_OUT` 分支生成。样本此前未把 `FILTERED_OUT` 列入允许原因，因此两个保守拒答被误判失败。
- 旧 `offline_scores.csv` 没有保存 `answerStatus`、`refusalReason` 和具体失败检查，且 `run_config.json` 仍显示过期门槛 `boundary_passed: 2 / 2`。新输出会保留这些字段，并按实际样本数显示 `5 / 5`。
- 10 条主样本在旧输出中都是期望文档排名第 1，因此按新增文档级口径可重建出 MRR、Recall@1/@3/@5、NDCG@5 均为 `1.0`。这主要反映当前“一问对应一篇目标笔记”的简单 qrels，不能替代后续多相关文档和 hard negative 样本。

修复评估口径后，`B03` 必须由 RAG 回答准入链路真正返回结构化拒答才能通过；评测器不再用模糊文案替系统掩盖该问题。

### 2026-07-18 最终 current 复验

使用生产同款 `pgvector + text-embedding-v4 + qwen3-rerank + qwen-plus` 重新索引 10 篇隔离资料并执行 15 条用例，输出目录为本地临时路径 `tmp/ragas-gap-fixes-20260718-final`，结果如下：

| 指标 | 结果 |
| --- | --- |
| 主样本 Top-1 / Top-3 | `10 / 10`、`10 / 10` |
| MRR / Recall@1 / Recall@3 / Recall@5 / NDCG@5 | 全部 `1.0` |
| evidence 引用结构 | `10 / 10` |
| 边界样本 | `5 / 5` |
| 主样本空 evidence | `0` |
| 离线总门禁 | `offline_passed=true` |

`B03` 的 DashScope rerank Top 分在复验中低于严格准入门槛 `0.55`，最终返回 `answerStatus=REFUSED`、`refusalReason=LOW_CONFIDENCE`、`evidences=[]`。`run_config.json` 同时记录 weighted RRF、通道与查询权重、本地重排权重和回答准入阈值，后续对比必须以这些参数为准。

当前中文关键词覆盖仍使用单字切分并合并 Multi-Query 扩展词，跨域问题可能出现覆盖率虚高；本轮依靠 rerank 绝对分门槛保守拒答。后续扩充 hard negative 和多相关文档 qrels 后，应重新校准 `0.55`，再决定是否升级中文关键词覆盖算法。

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
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode offline
```

当前项目 RAG 全流程离线验收：

```powershell
$env:PYTHONPATH='ai-python'
$env:RAG_VECTOR_DIMENSIONS='1024'
$env:RAGAS_TEST_TABLE_PREFIX='Ragas_Test_'
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode offline --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-offline
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
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode ragas
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
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode ragas --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-ragas
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
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode ragas --skip-index --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-ragas-reuse-index
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
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode ragas --skip-index --rag-profile current --case-index 1 --output-dir tmp/ragas-single-case-faithfulness-r01
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
| `offline_scores.csv` | 仅 `--mode offline` 生成，包含文档级 MRR/Recall/NDCG、命中排名、引用结构、边界状态/原因/失败检查和关键点覆盖结果 |
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

MRR、Recall@1/@3/@5 和 NDCG@5 首轮作为可观测基线写入 `offline_scores.csv`、`manual_review.md` 与 `run_config.json`，暂不单独设阻断阈值。当前主样本每条通常只有一个期望文档，样本量也只有 10 条；待扩充多相关文档 qrels 后，再用固定基线差异设置门槛，避免在小样本上制造虚假的高精度结论。

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
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode offline --rag-profile current --output-dir tmp/ragas-small-eval-current-rag-offline
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_ragas_small_eval.py --mode ragas --output-dir tmp/ragas-small-eval-missing-config
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
