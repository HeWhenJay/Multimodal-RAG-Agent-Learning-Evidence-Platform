# Agent 运行态效率复测方案

## 1. 目的与边界

本方案用于替换简历中缺少可复算证据的 Token 主指标、极端 API P95 和“故障注入一致性 100%”。复测只回答两个问题：

1. Redis L2 热态快照能否稳定缩短 `LocalAgentGateway.restore_context()` 的上下文恢复时间。
2. PostgreSQL 单事务 `append_turn_and_requeue()` 能否稳定缩短“追加消息并重排任务”的数据库链路时间。

本方案不评估模型生成质量、模型网络时延、Prompt Token、Kafka 端到端吞吐或完整 HTTP 请求时延。Redis 是可丢失的效率层，PostgreSQL 仍是唯一权威事实源。

## 2. 被测实现

| 能力 | 生产实现 | 对照路径 |
| --- | --- | --- |
| 上下文恢复 | `ai-python/agents/gateway/local_gateway.py::LocalAgentGateway.restore_context` | Redis L2 命中与删除缓存后的 PostgreSQL 回源 |
| 运行态缓存 | `ai-python/app/agent_runtime/runtime_state_cache.py::AgentRuntimeStateCache` | 真实 Redis，独立 DB 和测试前缀 |
| 会话原子续跑 | `ai-python/app/agent_runtime/repository.py::PostgresAgentRepository.append_turn_and_requeue` | `append_message` 后再调用 `update_task` 的拆分事务基线 |

执行脚本：`_qa_resume_memory_ab/run_runtime_efficiency_benchmark.py`。

## 3. 环境门禁

- Python 命令必须运行在 `learning-evidence-rag` conda 环境。
- `RAG_DATABASE_URL` 必须指向真实 PostgreSQL；禁止内存仓储和 SQLite 替代。
- `REDIS_URL` 必须指向真实 Redis；禁止 `FakeRedis`、字典或进程内缓存替代。
- Redis 使用隔离数据库；运行前必须为空，运行后删除本次测试键。
- PostgreSQL 测试任务标题和 ID 使用 `runtime-efficiency-v1` 前缀；报告落盘后删除本次测试任务，依赖外键级联清理消息和摘要。
- 环境快照记录 Python、psycopg、redis-py、PostgreSQL、Redis、操作系统和脚本 SHA-256，不记录密码或完整连接串。

## 4. 样本与输入

### 4.1 公共规则

- 固定随机种子：`20260731`。
- 预热：20 组配对调用，不计入正式统计。
- 正式样本：300 组配对调用。
- 顺序：每组随机决定 A→B 或 B→A，并强制两种顺序各 150 组。
- 连续时间窗口：按执行顺序划分 6 个窗口，每窗 50 组，观察运行期间不同时间段的方向稳定性。本文的“时间窗口”与 RAG 文档递归切块无关。
- 不删除、截尾或 Winsorize 任何正式样本；超时和异常按失败样本保留。

### 4.2 Redis 上下文恢复输入

- 1 个隔离 Agent 任务，固定 `pythonThreadId`。
- 120 条冻结长会话消息，覆盖岗位目标、简历证据、工具审批、学习计划、回答格式、时间约束、证据边界和近期覆盖 8 类内容。
- 每条消息包含场景编号、轮次编号和长度确定的中文正文，输入生成规则与最终文本写入 `fixture.json`。
- 1 条 ACTIVE 会话摘要覆盖前 80 条消息；`rawMessageFetchLimit=200`、`summaryLimit=6`、`bestWindowTokens=4000`、`summaryTargetTokens=400`。
- PostgreSQL 组：测量前删除本任务 Redis L2 键，调用生产 `restore_context()`，要求 `restoreSource=postgresql`。
- Redis 组：使用同一 PostgreSQL 重建结果预填 L2，再调用生产 `restore_context()`，要求 `restoreSource=redis_l2`。
- 两组仅允许 `budgetMetadata.restoreSource` 不同；移除该字段后规范化 JSON 的 SHA-256 必须一致。

### 4.3 PostgreSQL 单事务输入

- 为拆分事务组和单事务组各创建 1 个隔离任务。
- 每组调用写入唯一 `dedupe_key`、相同长度的用户消息、相同大小的 `input_json`，并把任务重置为 `CREATED`。
- 基线依次调用 `append_message()` 和 `update_task()`，形成两个独立数据库事务。
- 当前实现调用 `append_turn_and_requeue()`，在一个数据库事务内完成消息追加、任务状态和目标更新。
- 正式样本结束后，对单事务组执行 30 次已存在 `dedupe_key` 的重放；消息行数不得增加。重放耗时留在审计文件中，但不与唯一写入混合计算主指标。

## 5. 原始输出

每次运行创建 `_qa_resume_memory_ab/runtime_efficiency_v1/<runId>/`：

| 文件 | 内容 |
| --- | --- |
| `fixture.json` | 冻结输入、随机顺序、任务配置和测试 ID |
| `redis_restore_samples.csv` | 逐配对 Redis/PostgreSQL 耗时、顺序、来源、上下文哈希和错误 |
| `transaction_samples.csv` | 逐配对拆分/单事务耗时、顺序、配对降幅和错误 |
| `idempotency_replay_samples.csv` | 30 次重复键重放耗时和重放前后行数 |
| `summary.json` | P50、P95、均值、标准差、配对降幅、95% bootstrap CI、连续时间窗口结果和门禁 |
| `report.md` | 可阅读的输入、输出、公式、结果、限制和简历可用文案 |
| `environment.json` | 脱敏环境与版本快照 |

## 6. 计算公式

对任一耗时数组 `x`：

- `P50 = percentile(x, 50)`，采用排序后的线性插值。
- `P95 = percentile(x, 95)`，采用排序后的线性插值。
- `样本标准差 = sqrt(Σ(xᵢ - mean(x))² / (n - 1))`。
- `配对降幅ᵢ = (baselineᵢ - optimizedᵢ) / baselineᵢ × 100%`。
- `总体配对降幅 = median(配对降幅ᵢ)`。
- `方向一致率 = count(optimizedᵢ < baselineᵢ) / n`。
- `P95 降幅 = (P95_baseline - P95_optimized) / P95_baseline × 100%`。
- `窗口 j = pair[50×(j-1)+1 : 50×j]`，`j∈[1,6]`；每个窗口独立计算基线与优化组 P95，再按上式计算窗口 P95 降幅。
- `连续时间窗口 P95 降幅范围 = [min(六个窗口降幅), max(六个窗口降幅)]`，仅用于检查时序稳定性，不是 RAG 切块指标，也不是总体主效应。
- 95% bootstrap CI 对 300 个配对降幅进行 10,000 次有放回抽样，报告 bootstrap 中位数的 2.5% 和 97.5% 分位数。

上述重复调用用于估计当前机器和当前依赖下的运行时分布，不冒充 300 个独立用户或 300 个独立业务场景。

## 7. 通过门禁

### Redis 恢复

- 300/300 PostgreSQL 调用来源为 `postgresql`，300/300 热态调用来源为 `redis_l2`。
- 300/300 配对的规范化上下文哈希一致。
- 6 个连续时间窗口中至少 5 个窗口的 P50 和 P95 均为 Redis 更低。
- 总体 Redis P95 必须低于 PostgreSQL P95，且配对降幅 bootstrap 95% CI 下界大于 0。

### PostgreSQL 单事务

- 两组正式消息增量相同，任务状态和输入目标一致。
- 30 次幂等重放前后消息行数不变。
- 6 个连续时间窗口中至少 5 个窗口的 P50 和 P95 均为单事务更低。
- 单事务 P95 必须低于拆分事务 P95，且配对降幅 bootstrap 95% CI 下界大于 0。

任一门禁失败时，报告必须保留全部结果，但不得生成简历量化文案。

## 8. 简历写入规则

- 第 3 条只可写“Redis L2 上下文恢复耗时”，不得写模型回答时延、端到端时延或 Token 成本。
- 第 4 条只可写“消息追加与任务重排数据库链路”，不得外推为整个 API、Kafka 或 Outbox 的 P95。
- 简历优先写 P95 前后值和降幅；样本规模写为“300 组随机交错配对调用”，不得写成“300 个用户”。
- 正确性门禁用“上下文哈希一致”“幂等重放不增行”等事实描述，不再使用 `100%` 作为主要成果数字。

## 9. 本轮结果

正式运行：`_qa_resume_memory_ab/runtime_efficiency_v1/runtime-efficiency-20260731-161836/`。结果由 `summary.json`、逐调用 CSV 和 `report.md` 交叉复算，未删除异常值。

| 子测试 | 基线 P50/P95 | 当前实现 P50/P95 | P95 降幅 | 配对降幅中位数 | 95% bootstrap CI | 6 个连续时间窗口 P95 降幅范围 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Redis L2 恢复 | 288.49 / 369.40ms | 25.66 / 45.43ms | 87.70% | 91.13% | 90.89%-91.31% | 86.17%-88.96% |
| PostgreSQL 单事务 | 79.22 / 104.22ms | 31.50 / 49.71ms | 52.30% | 59.09% | 58.04%-60.12% | 48.73%-55.82% |

两项均为 `300/300` 有效配对，6/6 连续时间窗口的 P50 与 P95 方向一致。这里的窗口是按执行顺序划分的 50 组样本时段，与 RAG 文档切块无关。Redis 两组规范化上下文哈希全部一致，恢复来源分别为 `postgresql` 与 `redis_l2`；单事务组 30 次幂等重放前后消息数均为 `321`。这些是正确性门禁，不在简历中写成 `100%` 成果。

可写入简历的范围仅限：Redis L2 上下文恢复 P95 `369.40ms→45.43ms（-87.7%）`；PostgreSQL 消息追加与任务重排 P95 `104.22ms→49.71ms（-52.3%）`。不得外推为模型、完整 API、Kafka 或端到端时延。
