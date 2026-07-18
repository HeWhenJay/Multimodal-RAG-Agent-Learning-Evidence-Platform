# 模型微调与可学习融合实施清单

更新日期：2026-07-18

## 文档目的

本文只记录后续需要人工准备数据、算力或模型选择后再实施的训练任务，不表示这些能力已经完成。当前在线服务继续使用 FastAPI，Java 继续负责业务状态、权限和统一响应；训练逻辑只能位于 Python 离线训练边界，不能迁移到 Java。

建议项目主线命名为：**面向学习证据与岗位适配的自适应多模态检索融合**。

## 当前基线

- [x] 已实现 Multi-Query、BM25、pgvector 向量召回和 RRF/RAG-Fusion。
- [x] 已实现百炼 rerank，并保留确定性本地降级。
- [x] 已保留标题、章节、页码、幻灯片、时间戳、来源和分数等 evidence 字段。
- [x] 已实现 Ragas 小样本评估入口。
- [ ] 尚未形成可训练的 `query-positive-negative` 或多级相关性 qrels 数据集。
- [ ] 尚未保存所有候选的逐路 raw score、rank、模型版本和索引版本。
- [ ] 尚未实现领域 Cross-Encoder、双塔 embedding 或视觉检索模型训练。
- [ ] 尚未实现模型注册、灰度发布、回滚和双索引切换。

## 强制原则

- [ ] 先建立固定测试集和基线，再开始任何训练。
- [ ] 数据集按整份资料、课程、项目、视频或 JD 分组切分，禁止同源 chunk 跨 train/dev/test。
- [ ] LLM 生成的问题或 teacher 分数只允许作为训练弱标签；dev/test 必须人工复核。
- [ ] 训练、验证和线上推理都记录模型版本、数据版本、配置 hash、随机种子和 artifact checksum。
- [ ] Embedding 模型切换必须全量重建对应索引，禁止新旧向量空间混用。
- [ ] RRF 和当前百炼路径始终保留为 fallback 与对照基线。
- [ ] 简历、聊天和学习资料包含隐私数据，训练前必须取得授权并执行脱敏、最小化和可删除治理。
- [ ] Python 训练命令统一在 `learning-evidence-rag` conda 环境执行。
- [ ] 不提供公网 `/train` 接口；训练只能通过离线 CLI 或受控任务执行。

## 阶段 0：数据与评测基座

### 0.1 qrels 数据模型

- [ ] 定义 `qrels.jsonl`，至少包含以下字段：

```json
{
  "queryId": "q-001",
  "queryText": "这段项目经历能否证明具备 RAG 召回优化能力？",
  "documentId": "project-001",
  "chunkId": "project-001-chunk-08",
  "relevance": 3,
  "queryType": "jd_requirement",
  "modality": "text",
  "split": "train",
  "annotator": "human",
  "rationale": "包含可运行实现、评测指标和结果"
}
```

- [ ] `relevance` 使用统一四级标注：`0=无关`、`1=提及但不能证明`、`2=部分证明`、`3=充分证明`。
- [ ] `queryType` 至少覆盖事实问答、技能证明、JD 原子要求、学习缺口、视觉页面、视频时间点和无答案拒答。
- [ ] `modality` 至少覆盖 `text`、`page_image`、`video_frame`、`asr`、`ocr`、`code`、`resume`。
- [ ] 建立双人抽样复核与冲突处理规则，记录标注一致率。

### 0.2 数据规模

- [ ] Pilot：不少于 500 个 query，每个 query 标注 Top-20 至 Top-50 候选。
- [ ] 可公开结论版本：1,000 至 3,000 个 query。
- [ ] Cross-Encoder 可信训练版本：5,000 至 20,000 个 query，约 20,000 至 100,000 个 pair。
- [ ] 双塔 embedding 可信训练版本：10,000 至 50,000 个 query-positive，并为每条准备 5 至 15 个 hard negative。
- [ ] 视觉检索 Pilot：2,000 至 5,000 个 query-page 或 query-keyframe 正对；可信版本不少于 10,000 对。
- [ ] 生成模型 SFT：3,000 至 10,000 条高质量“问题 + evidence -> 引用回答/拒答”样本。
- [ ] DPO：只有在获得真实偏好后准备 1,000 至 5,000 组 chosen/rejected。

### 0.3 Hard negative 规则

- [ ] 从当前 BM25、向量和 RRF 的 Top-2 至 Top-50 中挖掘 hard negative。
- [ ] 覆盖“同技能但深度不足”“同项目但内容无关”“只写关键词无实施证据”“同视频相邻但错误时间段”。
- [ ] 覆盖“同页布局相似但图表结论不同”“OCR 文本相近但视觉对象不同”。
- [ ] 对自动挖掘的负例抽样复核，防止 false negative。
- [ ] teacher 模型分数只能生成 silver 数据，gold test 不使用 teacher 自动标签。

### 0.4 固定评测矩阵

- [ ] 固定以下版本并保存同一测试集结果：
  - BM25 only
  - dense only
  - BM25 + dense + 等权 RRF
  - 确定性加权融合
  - RRF + 通用 reranker
  - 学习融合
  - 微调 reranker
  - 微调 embedding
  - OCR-only、visual-only、text + visual fusion
- [ ] 检索指标包含 `Recall@5/10/20/50`、`MRR@10`、`NDCG@5/10` 和 `MAP`。
- [ ] 回答指标包含 Context Precision/Recall、Faithfulness、Answer Relevancy、引用 Precision/Recall 和拒答 F1。
- [ ] 工程指标包含索引时间、索引大小、吞吐、GPU 峰值、P50/P95 延迟和单次成本。
- [ ] 使用 paired bootstrap 或 randomization test，不能只报告单次平均值。

## 阶段 1：可学习融合排序

说明：这一阶段属于 Learning-to-Rank，不是基础模型微调，但它是当前等权 RRF 升级到可训练系统的最低成本路径。

- [ ] 先用 `ranx` 搜索归一化方法和静态权重，建立 weighted-sum 基线。
- [ ] 再用 LightGBM `lambdarank` 或 `rank_xendcg` 训练 query-group 排序模型。
- [ ] 特征至少包含：
  - BM25 raw score、归一化分数和 rank
  - dense cosine score、归一化分数和 rank
  - RRF contribution 与命中查询变体数量
  - 原始 query / 生成 query 标记
  - 文档类型、evidence channel、block type、summary/raw 标记
  - parse quality、OCR/ASR 质量、父段聚合状态和资料时效
  - 当前 rerank score 与回答准入诊断
- [ ] 所有归一化只能在单个 query 的候选集合内完成，避免跨 query 分数不可比。
- [ ] 导出模型为只读 artifact，并记录 feature schema version。
- [ ] 未加载模型、模型损坏或特征缺失时自动退回 RRF。
- [ ] 验收：held-out `NDCG@10` 相对等权 RRF 提升至少 3% 至 5%，`Recall@50` 下降不超过 1 个百分点。

推荐参考：

- [ranx](https://github.com/AmenRa/ranx)
- [LightGBM](https://github.com/lightgbm-org/LightGBM)
- [RAG-Fusion](https://github.com/Raudaschl/rag-fusion)

## 阶段 2：领域 Cross-Encoder reranker

- [ ] 先比较通用中文/多语 reranker，不直接训练。
- [ ] 8GB 显存环境优先从较小的 BGE Cross-Encoder 开始，使用短序列、小 batch、梯度累积和必要的 LoRA。
- [ ] `bge-reranker-v2-m3` 等更大模型只在 16GB 至 24GB 以上环境评估。
- [ ] 输入候选控制在 Top-20 至 Top-50，最终输出 Top-5 至 Top-10。
- [ ] 训练样本保留多级相关性，不把所有正例压成单一二分类标签。
- [ ] 测试长 chunk 截断、中文术语、代码片段、OCR 噪声和视频时间段场景。
- [ ] 本地模型失败、超时或内存不足时退回百炼 rerank，再退回确定性 reranker。
- [ ] 响应 diagnostics 返回 provider、model version、candidate count、耗时和 fallback reason。
- [ ] 验收以 `NDCG@5/10`、`MRR@10` 和端到端 Context Precision 为主，pair accuracy/AUC 只作辅助。

推荐参考：

- [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
- [Sentence Transformers](https://github.com/huggingface/sentence-transformers)
- [RAGatouille](https://github.com/AnswerDotAI/RAGatouille)

## 阶段 3：双塔 Embedding 微调

- [ ] 先确认当前通用 embedding 在自有 held-out 数据上的主要 bad case，再决定是否训练。
- [ ] 选择支持中文、非对称 query-passage 检索和 1024 维输出的候选模型，降低 schema 迁移成本。
- [ ] 使用 InfoNCE、in-batch negatives 和 hard negatives；必要时加入 teacher score distillation。
- [ ] 混入少量通用中文检索数据，监控领域训练后的通用能力退化。
- [ ] 为每个 embedding artifact 记录维度、最大长度、query/passage instruction 和归一化方式。
- [ ] 建立新旧双索引，完成全量 re-embed 和校验后再切换 active index。
- [ ] 禁止用新模型 query embedding 检索旧模型 document embedding。
- [ ] 回滚时同时切换模型和对应索引，不能只回滚模型文件。
- [ ] 验收以 rerank 前的 Recall@10/20/50、MRR、NDCG、吞吐和索引成本为主。

推荐参考：

- [Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding)
- [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
- [MTEB](https://github.com/embeddings-benchmark/mteb)

## 阶段 4：原生视觉与多模态检索

- [ ] 保留现有 OCR/ASR 文本通道，新增并行视觉检索通道，不直接替换稳定链路。
- [ ] 第一批索引对象选择 PDF/PPT 页面截图、图表、代码截图和视频关键帧。
- [ ] 先跑零样本模型，证明 visual-only 或 fusion 对 OCR 失败子集有增益后再训练。
- [ ] 单向量模型使用独立 visual embedding 表和固定维度索引。
- [ ] ColPali/ColQwen late interaction 使用独立 multi-vector 存储，不强塞进当前单列 `VECTOR(1024)`。
- [ ] 训练时按整份课件或整段视频切分，禁止相邻页面/帧泄漏到不同集合。
- [ ] hard negative 优先选择同课件相邻页、同视频邻近帧、相似布局和相似 OCR 文本。
- [ ] 对表格、公式、图表、布局和低质量 OCR 建立视觉 hard set。
- [ ] 报告 OCR-only、visual-only、text + visual fusion 三组消融。
- [ ] 8GB 显存不直接训练 2B VLM；24GB 只做低分辨率 QLoRA Pilot，稳定训练优先使用更大显存或租用短时 GPU。
- [ ] 验收包含 `Recall@10`、`NDCG@5/10`、ViDoRe、本地视觉 hard set、索引大小和 P95 延迟。

推荐参考：

- [ColPali](https://github.com/illuin-tech/colpali)
- [VLM2Vec](https://github.com/TIGER-AI-Lab/VLM2Vec)
- [Qwen3-VL-Embedding](https://github.com/QwenLM/Qwen3-VL-Embedding)
- [ViDoRe Benchmark](https://github.com/illuin-tech/vidore-benchmark)

## 阶段 5：引用回答 SFT 与偏好对齐

- [ ] 只训练回答格式、引用选择、拒答和证据化表达，不把时效知识烘进参数替代 RAG。
- [ ] SFT 输入固定为“问题 + 已检索 evidence + 输出约束”，目标包含结构化引用。
- [ ] 同时覆盖有答案、证据不足、弱片段、只有 summary 和跨权限过滤场景。
- [ ] 验证模型不能引用未返回的 evidenceId。
- [ ] DPO 只使用真实用户偏好或严格人工构造偏好，不使用未经复核的 LLM 自评偏好。
- [ ] chosen/rejected 主要区别应是引用正确性、事实忠实度、覆盖度和拒答合理性，避免只学长度偏好。
- [ ] 验收包含引用 Precision/Recall、Faithfulness、Answer Correctness、拒答 F1、JSON/schema 有效率和 P95。

推荐参考：

- [TRL](https://github.com/huggingface/trl)
- [PEFT](https://github.com/huggingface/peft)
- [LLaMA-Factory](https://github.com/hiyouga/LlamaFactory)

## 模型注册、发布与回滚

- [ ] 新增 model manifest，字段至少包含：`modelId`、`task`、`version`、`baseModel`、`dimensions`、`datasetVersion`、`featureSchemaVersion`、`artifactSha256`、`createdAt`。
- [ ] 训练依赖放在独立 `ai-python/requirements-training.txt`，不污染在线最小运行依赖。
- [ ] 模型文件不直接提交 Git；使用 artifact store 或 Git LFS，并在仓库提交 model card 和 checksum。
- [ ] 健康检查和 diagnostics 显示当前 fusion、embedding、reranker、visual 模型与索引版本。
- [ ] 发布支持 `shadow -> canary -> active -> retired` 状态。
- [ ] Shadow 阶段不影响用户结果，只记录候选差异、延迟和失败率。
- [ ] Canary 阶段按固定用户比例或实验组切流，不能随机混用索引。
- [ ] 每个模型配置明确超时、最大候选数、GPU/CPU device 和 fallback 顺序。
- [ ] 建立一键回滚到上一个模型与索引组合的操作说明。

## 在线反馈与隐私治理

- [ ] evidence 反馈至少支持“有效 / 部分有效 / 无效”，并允许选择原因。
- [ ] 回答反馈与 evidence 反馈分开保存，避免把“文案不喜欢”误当成检索负例。
- [ ] 保存曝光位置和候选集合，校正点击/位置偏差。
- [ ] 用户反馈先进入弱标签池，不能未经复核直接进入 gold test。
- [ ] 提供训练数据删除和重新生成能力，响应用户删除资料或账号的请求。
- [ ] 训练导出前移除姓名、电话、邮箱、学号、公司内部信息和密钥。
- [ ] 数据卡记录来源、授权、许可、语言、模态、已知偏差和不可用场景。

## 推荐目录边界

```text
ai-python/
  training/
    datasets/
    fusion/
    reranker/
    embedding/
    multimodal/
    generation/
    evaluation/
  model_registry/
  requirements-training.txt
```

- [ ] `training/` 只包含离线数据、训练、评测和导出。
- [ ] `rag/` 只加载已发布 artifact 并负责在线 fallback。
- [ ] Java 只管理反馈、权限、任务状态和模型发布业务状态。
- [ ] React 只提供标注、实验结果和发布状态界面，不直接调用 Python。

## Flask 备选边界

当前项目不新增 Flask 服务。只有模型与 benchmark 已经可以独立发布、并且需要独立 GPU 生命周期时，才抽取 companion repository。该服务只提供：

- `GET /health`
- `POST /embed`
- `POST /rerank`
- `POST /match`

训练仍由离线 CLI 执行，不提供 `/train`。当前 FastAPI 通过版本化客户端调用 Flask companion service，Java 和前端不绕过 FastAPI。

## 最终完成定义

只有同时满足以下条件，才可以在 README 或答辩中声明“已完成领域微调/多模态融合训练”：

- [ ] 训练代码、锁定依赖和可复现命令已提交。
- [ ] 训练数据 schema、数据卡、split 规则和 checksum 已发布。
- [ ] 固定 held-out benchmark、基线、消融和显著性检验已发布。
- [ ] 模型卡包含适用范围、限制、算力、隐私和许可证。
- [ ] 在线服务返回真实模型/索引版本，并具有 fallback 和回滚。
- [ ] 相比未训练基线达到预先约定的质量门槛，且延迟与成本在预算内。
- [ ] 真实用户数据可删除，训练产物具备对应的再训练或下架流程。
