# Kafka 故障恢复与死信处理

## 适用范围

本文描述纯 Python RAG 索引、资料分片收尾和索引终态同步的可靠性约定。查询接口不依赖 Kafka；
Kafka 不可用时由 PostgreSQL 耐久任务 worker 执行同一索引状态机。

## 投递与恢复

- Python 控制面在一个 PostgreSQL 事务内同时写入资料状态、`rag_index_job` 和投递记录。Kafka 模式写入
  `rag_outbox_event`，local 模式写入 `delivery_mode='LOCAL'` 的索引 job；两种模式均不使用 FastAPI
  `BackgroundTasks`、Java HTTP fallback 或内存队列。
- 已进入 Outbox 的任务不会改投 HTTP。Broker 暂时不可用时保留并指数退避重试；服务或 Broker 重启后，过期
  `PUBLISHING` 租约会重新投递。Kafka 关闭时 `app.workers.rag_task_worker` 用 `FOR UPDATE SKIP LOCKED`
  和 `lease_until` 抢占 local job，崩溃后可继续恢复。
- Kafka 采用至少一次投递。Python 用 `rag_consumed_event` 去重，staging / promote 以 jobId、
  requestVersion 和幂等键防止旧消息覆盖新索引。`app.workers.outbox_publisher` 对到期
  `NEW/FAILED` 和租约过期 `PUBLISHING` 记录使用 `FOR UPDATE SKIP LOCKED` 抢占，Kafka 确认成功后才写
  `PUBLISHED`。单轮事件按 `(topic, message_key)` 分组，不同组用 16 个 I/O 线程并发发布，同组按事件 ID
  串行发送。
- Kafka consumer 的 poll 线程不执行索引业务；长任务进入受限线程池后，poll 继续维持 consumer group
  心跳。同一 topic-partition 内不同资料 key 可以并发执行，相同资料 key 仍严格串行；乱序完成的消息
  只有在前序 offset 全部完成后才提交连续水位。索引长任务和 progress/result/promote/DLQ 控制消息使用
  独立容量，避免长视频占满线程后阻塞自身状态收敛。
- 索引实际并发由 `RAG_KAFKA_HANDLER_CONCURRENCY` 限制，不再取决于
  `rag.material.index.request.v1` 的分区数。生产环境仍可按吞吐和 consumer 实例数规划 topic 分区，
  但本机单分区无需为了线程池并发而不可逆扩容。
- promote 成功先完成资料终态写回和 offset 提交，再由 `RAG_REVIEW_SYNC_WORKERS` 独立线程池执行
  LangExtract 与复习卡片 LLM；复习生成不会占住 promote-result 分区。
- `rag_index_job.locked_by/lease_until` 是 Kafka 与 local 索引共用的数据库执行围栏。重复投递在租约内只等待，
  worker 定时续租；失租后旧执行不得继续写资料元数据、staging 终态或 DLQ。local worker 每轮领取数不超过
  当前执行槽，避免任务尚在线程池排队时租约已经过期。

## 死信规则

- Python 索引请求遇到永久源文件错误，或重试次数耗尽时，会先发送失败结果，再发送脱敏后的 `RAG_INDEX_DLQ` 到 `rag.material.index.dlq.v1`。
- Python consumer 解析失败或业务异常会进行有限次重连；仍无法处理时发送 `RAG_KAFKA_CONSUMER_DLQ`。
  DLQ 消息及其 `log_error` 记录只保存资料 ID、job ID、源 topic、partition、offset、错误摘要和消息摘要哈希，
  不保存资料正文、Token 或对象存储密钥。
- Python DLQ writer 会记录 `log_error`；日志以脱敏定位摘要区分不同死信，同一 Kafka 消息重复投递只增加
  出现次数。对索引和 promote 等终态失败，会将对应 job / 资料标为 `FAILED` 或 `DLQ`，清除 active job，
  避免页面长期显示“解析中”。
- DLQ 不自动无限重放。修复源文件、权限或配置后，重新发起资料重建索引；这样会产生新的 jobId 和 requestVersion，避免旧消息覆盖已修复的资料。

## Redis 缓存一致性

- PostgreSQL 是 Agent 消息和摘要的唯一权威来源，Redis 只保存短期热态上下文和 SSE 缓冲。
- 事务中的消息或摘要写入会同时登记 `agent_cache_repair_task`，只在数据库提交后删除 Redis 上下文缓存。
- Redis 删除失败时，修复任务保留在数据库并定时重试；任务未修复前，读取路径绕过 Redis，直接从 PostgreSQL 重建上下文，因此不会读取旧缓存。

## 关键配置

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `RAG_KAFKA_RECONNECT_INITIAL_SECONDS` | `1` | Python worker 首次重连等待时间 |
| `RAG_KAFKA_RECONNECT_MAX_SECONDS` | `30` | Python worker 最大重连等待时间 |
| `RAG_KAFKA_PRODUCER_FLUSH_SECONDS` | `5` | Python producer 等待投递确认的最长时间 |
| `RAG_OUTBOX_PUBLISHER_ENABLED` | `true` | Kafka 模式下 Python Outbox 发布器所有权开关 |
| `RAG_OUTBOX_BATCH_SIZE` | `50` | Python 单轮抢占的最大 Outbox 事件数 |
| `RAG_OUTBOX_PUBLISH_CONCURRENCY` | `16` | 不同 topic/key 的 Outbox Kafka/数据库 I/O 并发；同组保序 |
| `RAG_OUTBOX_LEASE_SECONDS` | `60` | Python 领取 Outbox 事件后的租约时长 |
| `RAG_OUTBOX_PUBLISH_FIXED_DELAY_MS` | `1000` | Python Outbox 单轮完成后的固定等待时间 |
| `RAG_OUTBOX_MAX_ATTEMPTS` | `8` | Python 指数退避最大指数，不限制最终重试次数 |
| `RAG_KAFKA_PUBLISH_TIMEOUT_MS` | `3000` | Python 单条 Outbox 等待 Kafka 确认的最长时间，最小 100ms |
| `RAG_KAFKA_HANDLER_CONCURRENCY` | `9` | 单个 Kafka worker 的 CPU/内存密集索引长任务并发上限 |
| `RAG_KAFKA_CONTROL_CONCURRENCY` | `16` | progress/result/promote/DLQ 控制消息 I/O 保留线程数 |
| `RAG_INDEX_EXECUTION_LEASE_SECONDS` | `180` | Kafka/local 索引执行令牌租约时长 |
| `RAG_TASK_WORKER_ENABLED` | `true` | 是否启动 PostgreSQL 查询/local 索引耐久 worker |
| `RAG_TASK_WORKER_POLL_SECONDS` | `1` | durable worker 空闲轮询间隔 |
| `RAG_TASK_WORKER_BATCH_SIZE` | `9` | 每轮抢占的查询/local 索引任务数 |
| `RAG_TASK_WORKER_CONCURRENCY` | `9` | 查询/local 索引 CPU/内存密集耐久任务并发槽 |
| `RAG_TASK_WORKER_LEASE_SECONDS` | `120` | durable worker 任务租约时长 |
| `RAG_QUERY_TASK_TTL_SECONDS` | `1800` | 未完成查询任务转为 `EXPIRED` 的时间 |
| `RAG_REVIEW_SYNC_WORKERS` | `16` | 索引终态后自动复习生成 I/O 线程数 |
