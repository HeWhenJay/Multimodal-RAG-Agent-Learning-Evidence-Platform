# Agent 三层记忆离线配对三臂评测方案 v4

更新日期：2026-07-31

## 1. 文档目的

本方案用于严谨比较以下三种长会话上下文组织方式：

| 实验组 | 上下文组成 | 要回答的问题 |
| --- | --- | --- |
| A：原始历史基线 | PostgreSQL 权威消息按时间排序，在统一上下文硬上限内确定性截断；不使用摘要 | 原始消息基线在长会话中的质量、噪声和成本怎样 |
| B：两层短期 | L1 最近窗口 + L2 Redis 热态恢复；不使用 L3 摘要或回捞 | 只保留近期状态时，会丢失多少跨窗口事实 |
| C：三层会话上下文 | 与 B 相同的 L1/L2，增加生产摘要、PostgreSQL 持久化及摘要覆盖范围回捞 | L3 能否补回跨窗口事实，同时控制噪声和成本 |

本方案不把“摘要注入效果”“摘要生成质量”“Redis/PostgreSQL 恢复能力”和“生产 Agent 端到端质量”混成一个实验。四类能力必须分层验证，只有端到端层通过后，才能形成线上收益结论。

运行态效率已经独立于三臂回答 Token/质量实验复测，详见 [`docs/testing/agent-runtime-efficiency-benchmark.md`](agent-runtime-efficiency-benchmark.md)。2026-07-31 的真实 Redis + PostgreSQL 结果只允许用于“上下文恢复链路”和“单事务消息重排链路”的时间描述，不再把 Prompt Token 作为简历主栏目。

现有 `raw-vs-two-layer-vs-three-layer-memory-v3` 结果保留为历史组件微基准。它使用冻结合成摘要、`FakeRedis` 与 `InMemoryAgentRepository`，评分器在整个模型 JSON 中做字符串匹配，因此只能解释为标记召回实验，不能解释为完整回答质量、真实数据库性能或线上 Token 收益。

### 1.1 详细输入、输出和计算在哪里

历史 v3 的自包含审计根目录为：

```text
_qa_resume_memory_ab/audit_v3
```

| 文件 | 直接用途 |
| --- | --- |
| `audit_index.md` | 首选入口；说明怎么查、哪些字段是真实保存、哪些字段无法恢复，并链接代表样本 |
| `detailed_samples.md` | 72 条调用的可全文搜索版本；每条包含完整逻辑请求体、解析输出、usage、时延和评分过程 |
| `samples/<sampleId>.json` | 单条自包含记录；适合逐字段核验，例如 `samples/career-v3-02__r1__two_layer_short_term.json` |
| `samples_self_contained.jsonl` | 同样 72 条记录的机器可读合并文件 |
| `sample_index.csv` | `scenarioId/arm/repetition` 到单样本 JSON 的索引 |
| `metric_calculation.csv` | 每条调用的指标分子、分母、联合判定、Token、时延和样本路径 |
| `aggregate_calculations.json` | 原表每个场景/组别数字的分子、分母、三次调用值与聚合公式 |
| `manifest.json` | 原始 JSONL 和审计产物的 SHA-256、字节数及捕获状态 |

v3 输入不是凭空补写：导出器使用冻结夹具和当时的 Prompt builder 重建 `contextPayload/systemPrompt/userPrompt/requestBody`，并逐条与历史 `contextPayloadHash/systemPromptHash/userPromptHash` 核对，当前为 `72/72` 一致。v3 输出中可确认的是当时已经落盘的解析后 JSON、usage、模型名、provider、`modelLatencyMs` 和重试次数。

下列字段当时没有保存，现已统一标记为 `notCapturedInV3`，不得把重建值或 canonical JSON 冒充为原始 Provider 证据：

- Provider 原始 HTTP body 和原始 `message.content` 的精确文本。
- HTTP 状态、响应头、DashScope requestId、completionId、finishReason 和返回模型版本。
- 单次 HTTP attempt 的开始/结束时间与纯 Provider 时延。
- 当时 usage 中被客户端丢弃的扩展字段。

### 1.2 原表数字的逐步计算示例

以 `career-v3-02` 第 1 次重复为例，期望早期标记为 `航标-23`，近期约束为 `可运行原型` 和 `不安排移动端开发`，作废值为 `旧航标-08`：

| 样本 | answer-only 召回 | answer-only 约束 | 作废值 | answer-only 联合 | Prompt Token | 历史 modelLatencyMs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `career-v3-02__r1__raw_history` | `1/1=100%` | `0/2=0%` | `0` | 失败 | `806` | `3014.957` |
| `career-v3-02__r1__two_layer_short_term` | `0/1=0%` | `2/2=100%` | `0` | 失败 | `257` | `2051.811` |
| `career-v3-02__r1__three_layer` | `1/1=100%` | `2/2=100%` | `0` | 通过 | `374` | `2433.800` |

原 v3 对第一行给出的约束分是 `2/2`、联合为通过，因为它扫描整个解析 JSON，`appliedRecentConstraints` 自报数组中包含两条约束；但用户可见 `answer` 没有逐条复述约束，所以 answer-only 重算应为 `0/2`、联合失败。这是禁止把 v3 `联合完成` 当作完整任务质量的直接反例。

用户所见逐场景表按同一场景、同一组的三次调用聚合：

```text
召回 = (r1 召回分 + r2 召回分 + r3 召回分) / 3
约束 = (r1 约束分 + r2 约束分 + r3 约束分) / 3
联合 = 三次调用中 joint=true 的次数 / 3
平均 Prompt = (r1 prompt_tokens + r2 prompt_tokens + r3 prompt_tokens) / 3
平均时延 = (r1 modelLatencyMs + r2 modelLatencyMs + r3 modelLatencyMs) / 3
```

因此 `career-02/2层` 为：召回 `(0+0+0)/3=0%`，约束 `(1+1+1)/3=100%`，联合 `0/3`，平均 Prompt `(257+257+257)/3=257.0`，平均时延 `(2051.811+2644.702+2053.137)/3=2249.883ms`，显示为 `2249.9ms`。`aggregate_calculations.json` 保存了其余 23 行的同样计算链。

## 2. 预注册假设

在运行模型前固定以下主要假设，完成后不得按结果临时更换主指标：

1. **跨窗口恢复假设**：C 组的跨窗口当前事实覆盖率高于 B 组。
2. **质量不劣假设**：C 组相对 A 组的任务完整率、忠实度和答案相关性不劣，非劣界限预设为 `-2pp`。
3. **冲突治理假设**：C 组不会提高作废信息泄漏率、未经上下文支持的事实率或越权工具建议率。
4. **成本假设**：只有计入摘要生成、检索、存储、输入与输出 Token 后，才判断 C 组是否降低端到端成本。
5. **恢复假设**：真实 Redis miss、Redis 不可用和 Worker 重启后，C 组均能从 PostgreSQL 恢复相同 `threadId`、有效摘要和必要原文。

## 3. 分层测试矩阵

| 层级 | 运行环境 | 摘要来源 | 存储 | 核心指标 | 可形成的结论 |
| --- | --- | --- | --- | --- | --- |
| L0 契约测试 | 纯 Python | 固定夹具 | 内存替身 | payload 隔离、哈希、评分公式、脱敏 | 实现契约正确 |
| L1 组件消融 | 真实回答模型 | 人工审核黄金摘要 | 内存替身 | 跨窗口覆盖、完整性、忠实度、Token | 摘要注入本身的增量 |
| L2 摘要生成 | 真实压缩模型 | 生产压缩节点生成 | 隔离 PostgreSQL | 原子事实保留、作废值治理、摘要忠实度 | 摘要器质量 |
| L3 恢复集成 | 不要求回答模型 | 生产摘要 | 真实 Redis + PostgreSQL | miss 回源、重启恢复、P50/P95、数据一致性 | 真实恢复能力 |
| L4 Agent 端到端 | 生产同款 LangGraph + Qwen | 生产摘要 | 真实 Redis + PostgreSQL | 任务完整性、工具边界、引用、总 Token/成本 | 可用于简历的离线端到端结论 |
| L5 灰度/线上 | 经审批的开发流量 | 生产链路 | 生产隔离命名空间 | 用户成功率、人工修订率、错误率 | 线上产品结论 |

L1 使用黄金摘要是为了单独回答“摘要信息进入 Prompt 后是否有效”；L2 再单独测试摘要是否生成正确。若直接用模型生成摘要做 L1，失败时无法区分是摘要器丢信息还是回答器不会使用摘要。

## 4. 场景数据集

### 4.1 先导试验与正式样本量

- 先用不少于 `30` 个开发场景估计三组配对不一致率，不把先导结果并入正式结论。
- 正式样本量按 exact McNemar 配对功效计算，要求双侧 `alpha=0.05`、`power>=0.80`、最小可检测差异 `10pp`。
- 在功效计算结果更小的情况下，正式集仍不得少于 `120` 个独立会话。
- 每个会话三组配对运行，最低重复 `3` 次，建议重复 `5` 次；重复只估计模型波动，不增加独立样本数。
- 最低真实回答调用量为 `120 × 3 × 3 = 1,080`；摘要生成和 Judge 调用另计。
- 主结果按场景配对计算，不能把同一场景的多次重复冒充独立业务样本。

### 4.2 场景覆盖

8 类任务每类至少 15 个正式场景：

1. 回答格式与表达偏好。
2. 岗位目标和学习优先级。
3. 简历事实与量化边界。
4. evidence 引用结构与证据不足处理。
5. 工具审批、HITL 和草稿边界。
6. 学习节律、时间和任务数量。
7. Python/Java 服务职责与状态同步。
8. 新旧值覆盖、冲突和未知信息拒答。

每个场景至少包含：

- `3` 个当前有效原子事实，其中至少 `1` 个位于最近窗口之外。
- `2` 个可执行近期约束，必须能通过明确谓词验证，而不是只检查短语是否被复述。
- `1` 个明确作废或冲突事实。
- `20-80` 条无关历史或 hard negative。
- `1` 个参考答案及原子事实清单。
- `1` 组允许同义表达与禁止表达。
- `1` 个完整任务判定器，覆盖问题要求的所有字段。

正式集使用自然业务事实，例如岗位方向、技能优先级、审批状态、简历证据、学习时间和服务职责，不再把“航标-23”一类唯一魔法词作为主要质量依据。允许保留少量精确标识符场景测试 ID/版本覆盖，但必须同时包含语义事实与行为约束。场景输入与 gold/rubric 分文件保存，生产 Prompt 构造代码不得读取 gold 文件。

记忆距离按以下比例分层：

| 距离类型 | 比例 | 目的 |
| --- | ---: | --- |
| 最近窗口重述 | 25% | 验证 B/C 的近期能力 |
| 仅窗口外存在 | 35% | 验证 L3 增量 |
| 新旧值冲突 | 20% | 验证作废信息治理 |
| 信息确实缺失 | 20% | 验证拒绝猜测与不确定性表达 |

### 4.3 数据冻结与防泄漏

- 场景使用 JSON 文件保存，不把唯一真值只写在 Python 代码中。
- 当前问题不得直接包含待召回答案。
- 评分字段不得出现在实验臂名称或系统提示中的示例答案里。
- 训练/调试集与最终保留集分离；最终保留集运行前不查看模型输出。
- 场景文件、系统 Prompt、判定器和模型配置分别计算 SHA-256。

## 5. 调用控制

- 三组必须经过同一个生产 Agent 入口和同一个上下文策略接口，仅通过预注册的策略开关切换 A/B/C；禁止继续用 benchmark 专属 `build_arm_payloads` 手工拼接主实验 Prompt。
- L4 主实验强制使用 `PostgresAgentRepository`、真实 Redis 和生产摘要节点；出现 `FakeRedis`、`InMemoryAgentRepository`、人工 `summary_text` 或摘要 fallback 时该样本失败。
- 三组共享相同模型、模型版本、系统 Prompt、当前问题、`temperature=0`、超时和重试策略。
- 三组调用顺序在全部 `6` 种排列中均衡；同一场景三组尽量在相邻时间窗口完成。
- 每条调用保存 `repetition`、`callPosition`、开始时间、结束时间和重试明细。
- 重试后的时延分别保存 `finalAttemptLatencyMs` 与 `endToEndLatencyMs`，不把退避时间混成单一指标。
- 发生限流、模型切换、usage 缺失或响应无法解析时，该配对样本标记为无效并整组重跑，不能只补跑表现较差的一组。

## 6. 每条调用必须保存的完整证据

每条记录使用稳定 `sampleId = scenarioId__r{repetition}__{arm}`，至少包含：

### 6.1 输入

- 场景原文、参考答案、原子事实、约束谓词和作废值。
- 完整 `systemPrompt`。
- 完整 `contextPayload`。
- 完整 `userPrompt`。
- 实际发送到 Chat Completions 的脱敏请求体，包括模型、messages、temperature 与 response_format。
- 输入、Prompt、场景和判定器哈希。

### 6.2 输出

- Provider 原始 `message.content`。
- 解析后的 JSON。
- HTTP 状态、白名单响应头、requestId、provider、模型与模型版本；不得保存 API Key。
- `prompt_tokens`、`completion_tokens`、`total_tokens`。
- 首次成功时延、端到端时延、重试次数与错误类别。

### 6.3 评分轨迹

- 每个参考原子事实是否由 `answer` 支持及命中证据位置。
- 每个近期约束谓词的输入、判定结果和失败原因。
- 每个作废值是否泄漏。
- 每个输出事实是否能在当前 payload 中找到依据。
- 自动评分、盲评、人工复核及最终裁决。
- 评分器版本、Judge 模型、Judge Prompt 哈希与人工评审人匿名编号。

### 6.4 v4 单样本自包含结构

每个 `samples/<sampleId>.json` 至少使用以下逻辑结构；数组可扩展，但字段不得在汇总后才反向补写：

```json
{
  "schemaVersion": "memory-ab-audit/1.0",
  "run": {
    "runId": "20260731T120000Z-<suffix>",
    "gitCommit": "<sha>",
    "gitDirty": false,
    "fixtureSha256": "<sha256>",
    "rubricSha256": "<sha256>",
    "scorerVersion": "v4.0"
  },
  "sample": {
    "sampleId": "career-v4-002__r1__three_layer",
    "scenarioId": "career-v4-002",
    "arm": "three_layer",
    "repetition": 1,
    "callPosition": 3
  },
  "input": {
    "scenarioFixture": {},
    "contextPayload": {},
    "systemPrompt": "<完整文本>",
    "userPrompt": "<完整文本>",
    "request": {
      "method": "POST",
      "url": "<脱敏后的 provider URL>",
      "headersAllowlist": {"content-type": "application/json"},
      "secretHeadersOmitted": ["authorization"],
      "body": {"model": "qwen-plus", "messages": [], "temperature": 0}
    },
    "expected": {
      "criteria": [
        {
          "criterionId": "constraint.maxItems",
          "dimension": "recentConstraintAdherence",
          "validator": {"type": "maxListItems", "max": 2, "targetJsonPath": "$.output.parsed.answer"}
        }
      ]
    }
  },
  "attempts": [
    {
      "attempt": 1,
      "startedAt": "<UTC ISO-8601>",
      "finishedAt": "<UTC ISO-8601>",
      "providerDurationMs": 1234.5,
      "backoffBeforeMs": 0,
      "httpStatus": 200,
      "requestId": "<provider request id>",
      "rawHttpBodyText": "<原始响应体>",
      "rawContent": "<原始 message.content>",
      "parsedOutput": {},
      "usageRaw": {},
      "usageNormalized": {"promptTokens": 374, "completionTokens": 119, "totalTokens": 493}
    }
  ],
  "score": {
    "criteria": [
      {
        "criterionId": "constraint.maxItems",
        "passed": true,
        "observedJsonPath": "$.output.parsed.answer",
        "observedExcerpt": "1. ... 2. ...",
        "reason": "列表项计数为 2，未超过上限"
      }
    ],
    "numerators": {"currentFact": 3, "recentConstraint": 2},
    "denominators": {"currentFact": 3, "recentConstraint": 2},
    "jointCompleted": true
  },
  "integrity": {
    "requestBodySha256CanonicalJson": "<sha256>",
    "responseBodySha256": "<sha256>",
    "scoringInputSha256CanonicalJson": "<sha256>"
  }
}
```

原始响应必须在收到每次 attempt 后立即追加写入 `provider-attempts.jsonl`，再进行解析和评分。即使解析失败、重试耗尽或进程中断，也必须保留已经发生的请求证据。

历史 v3 未捕获 Provider 原始 content、完整 HTTP body、requestId 和响应头，不能事后恢复。修订审计包必须把这些字段显式记为 `notCapturedInV3`，不得用解析后的 JSON 冒充原始 HTTP 响应。

## 7. 指标与计算

### 7.1 确定性硬指标

评分只读取最终 `answer` 和受控结构字段，不在模型自报的 `recalledEarlyFacts` 或 `appliedRecentConstraints` 中搜索答案。

```text
上下文目标覆盖率 = 实际 delivered context 中出现的 requiredCurrentFacts 数 / requiredCurrentFacts 总数

当前事实覆盖率 = answer 支持的 requiredCurrentFacts 数 / requiredCurrentFacts 总数

条件使用率 = answer 使用且正确表达的 requiredCurrentFacts 数 / delivered context 已覆盖的 requiredCurrentFacts 数

近期约束通过率 = 通过的 constraintPredicates 数 / constraintPredicates 总数

作废信息泄漏率 = 输出包含任一 obsoleteFact 的样本数 / 有作废事实的样本数

无依据事实率 = answer 中无法由当前 payload 或允许常识支持的原子事实数 / answer 原子事实总数

正确拒答率 = 信息缺失场景中明确拒绝猜测且未编造答案的样本数 / 信息缺失样本数

完整任务通过 = 当前事实覆盖率为 1
            且所有约束谓词通过
            且作废信息未泄漏
            且无依据事实率为 0
            且参考答案要求的字段全部回答
```

“上下文目标覆盖率”与“条件使用率”必须分开报告：前者低说明上下文组织、摘要或回捞没有把事实交给模型；前者为 1 而后者低，才主要指向回答模型没有正确使用已提供信息。

摘要器单独计算：

```text
摘要事实精度 = 有原始轮次来源支持的摘要原子事实数 / 摘要原子事实总数
摘要事实召回 = 摘要保留的有效 gold 原子事实数 / 有效 gold 原子事实总数
```

作废值只有在某组实际暴露给模型时，才进入该组的“暴露条件下作废采用率”；同时保留全样本泄漏率。否则 A 暴露作废历史而 B/C 未暴露时，直接比较会混入输入难度差异。

“完整任务通过”必须来自场景专用判定器；不能仅因几个代号和短语出现就通过。

### 7.2 语义质量指标

- **Faithfulness / Groundedness**：回答事实是否由输入上下文支持。
- **Answer Relevancy**：是否直接回答当前问题。
- **Completeness**：参考答案原子事实和问题字段是否完整覆盖。
- **Instruction Following**：格式、审批、数量和顺序约束是否真实落实。

使用隐藏实验臂名称、随机答案顺序的盲评。Judge 使用与回答模型不同且版本固定的模型；每个样本至少两次独立 Judge 评分。Judge 分歧超过 1 分或结论相反时进入人工复核。

人工抽检至少覆盖 `20%` 样本，并对所有硬失败、Judge 分歧和三组结论相反的样本全量复核。报告 Cohen's kappa 或 Krippendorff's alpha，不只报告平均分。

### 7.3 成本与性能

```text
回答输入 Token = answer call 的 prompt_tokens
回答输出 Token = answer call 的 completion_tokens
摘要生成 Token = compression call 的 prompt + completion tokens
端到端模型 Token = 回答输入 + 回答输出 + 摘要生成
端到端成本 = 各模型 Token × 运行时单价 + embedding/rerank 费用
```

同时报告：

- 每组 Token 总量、均值、中位数和 P95。
- 回答调用时延 P50/P95、摘要时延 P50/P95、恢复时延 P50/P95 和端到端时延 P50/P95。
- Redis 命中率、PostgreSQL 回源率、恢复失败率和 thread 保持率。

组件层只允许写“回答 Prompt 载荷”；只有 L4 端到端层才能写“总 Token”或“成本降低”。

单任务摊销成本必须计入摘要的实际复用次数：

```text
单任务总 Token = 回答输入 Token + 回答输出 Token
               + (摘要输入 Token + 摘要输出 Token + 抽取/Embedding Token) / 摘要实际复用次数
```

报告同时给出复用 `1` 次、实际复用次数及 break-even 复用次数。外部回答模型网络时延不得用于证明 Redis/PostgreSQL 本身更快；恢复性能必须以 Redis/PG、上下文组装、摘要和回答调用的分阶段时延报告。

## 8. 聚合与统计

- 先在同一场景内合并重复运行，再按场景宏平均，避免长答案或重复次数对结果加权过大。
- 二元配对指标使用 McNemar 精确检验或精确符号检验。
- 连续配对指标报告场景级配对差值，并使用不少于 `10,000` 次 bootstrap 给出 `95% CI`。
- 同时报告绝对值、百分点差和相对变化；`50% -> 100%` 写作 `+50pp`，不能写成“提升 100%”而不说明基线。
- 主指标、非劣界限和显著性方法预先写入 `run_config.json`。
- 主比较为 `C-B`；其余比较使用 Holm 方法校正多重检验。二元场景结论使用 exact McNemar，连续场景级配对差使用 `10,000` 次 cluster bootstrap。
- 语义 rubric 由两名盲化评审独立判定，要求 Cohen's kappa `>=0.80`；LLM Judge 只作辅助，不能作为唯一裁判。

## 9. 验收门禁

| 类别 | 正式门禁 |
| --- | --- |
| 审计完整性 | 100% 样本具备完整输入、输出、usage、评分轨迹和哈希 |
| 环境真实性 | L4 中 `FakeRedis=0`、`InMemoryAgentRepository=0`、人工摘要/fallback=0 |
| Schema | JSON 解析成功率 >= 99%，失败样本必须保留原始 content |
| 跨窗口恢复 | C-B 完整任务通过率点估计至少 +10pp，且配对差的 95% CI 下界 > 0 |
| 质量不劣 | C 相对 A 的完整任务通过率差值 95% CI 下界 >= -2pp |
| 近期约束 | C 相对 B 的近期约束通过率非劣界为 -2pp |
| 作废治理 | C 组作废信息泄漏率为 0 |
| 无依据事实 | C 组无依据事实率 <= A 组，且绝对值 <= 2% |
| 缺失信息 | C 组正确拒答率 >= 95% |
| 工具安全 | 未审批写工具下游调用为 0 |
| 真实恢复 | Redis miss/不可用/Worker 重启场景失败数为 0，且恢复上下文哈希与 thread 标识一致；百分比只作为门禁统计，不作为简历主成果 |
| 成本结论 | 只有端到端模型 Token 与费用的 95% CI 同时通过，才允许声明成本下降 |

任一安全硬门禁失败时，不允许用平均质量或 Token 优势抵消。

### 9.1 v4 完成前禁止使用的结论

在 L4 正式集通过且审计包完整前，不得在测试报告、README 或简历中写：

- “提升 50pp”“`n=24/组`”或“联合完成率提升”。
- “线上 A/B”“真实用户效果”或“生产时延下降”。
- “Redis 提高回答质量”或“完整长期记忆已上线”。
- “总成本下降”“100% 可靠”或未附样本数、95% CI 和数据性质的效果量。

当前可以严谨表述为：“构建 Agent 会话上下文三臂离线评测与逐调用输入输出审计框架，并定位历史评分器自报字段污染问题。”

## 10. v3 历史结果的正确解释

v3 的 72 条真实 `qwen-plus` 调用可以用于验证以下事实：

- 三组 Prompt 可以从冻结脚本确定性重建，现有哈希一致。
- `usage.prompt_tokens`、解析输出和单条标记计算可复算。
- 在当前 8 个合成场景中，L3 黄金摘要补回了两层窗口缺失的代号。

v3 不能证明：

- 完整任务质量提高 50pp。
- 生产摘要器能够稳定生成同等质量摘要。
- 真实 Redis/PostgreSQL 恢复性能。
- 线上 Token 或成本降低。
- Agent 的工具调用、规划或 evidence 引用整体质量提升。

v3 的独立样本数是 `8` 个场景，不是每组 `24` 个独立样本；三次重复只用于观察托管模型波动。v4 完成前，简历只应写“构建三臂离线评测与逐调用审计框架”，不应继续使用 `+50pp`、`n=24/组` 或“联合完成率提升”等效果表述。

## 11. 产物目录

建议每次运行使用独立目录：

```text
_qa_resume_memory_ab/v4/runs/<runId>/
├── manifest.json
├── run_config.json
├── dataset_snapshot.jsonl
├── summary_calls.jsonl
├── answer_calls.jsonl
├── provider-attempts.jsonl
├── scores.jsonl
├── aggregates.json
├── audit_index.md
└── samples/
    └── <sampleId>/
        ├── input.json
        ├── memory_trace.json
        ├── raw_response.json
        ├── normalized_output.json
        └── score.json
```

每个文件写入 SHA-256；`manifest.json` 保存 Git SHA、dirty 状态、Python 版本、依赖锁摘要、模型与 Judge 版本、Prompt/场景/评分器哈希，以及所有产物的相对路径和哈希。

## 12. 验证命令

Python 命令必须使用 `learning-evidence-rag` conda 环境：

```powershell
$env:PYTHONIOENCODING='utf-8'
conda run -n learning-evidence-rag python -B -m pytest _qa_resume_memory_ab -q
```

正式 v4 运行命令应在 runner 落地后固定为：

```powershell
$env:PYTHONIOENCODING='utf-8'
conda run -n learning-evidence-rag python -B _qa_resume_memory_ab/run_memory_ab_v4.py `
  --model qwen-plus `
  --repetitions 3 `
  --scenario-file docs/testing/agent-memory-ab-v4-cases.json `
  --output-dir _qa_resume_memory_ab/v4/runs/<runId>
```

未满足真实 Redis/PostgreSQL、Judge 配置或完整审计字段时，runner 必须返回非 0，不生成“通过”报告。
