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
  `PUBLISHED`。

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
| `RAG_OUTBOX_LEASE_SECONDS` | `60` | Python 领取 Outbox 事件后的租约时长 |
| `RAG_OUTBOX_PUBLISH_FIXED_DELAY_MS` | `1000` | Python Outbox 单轮完成后的固定等待时间 |
| `RAG_OUTBOX_MAX_ATTEMPTS` | `8` | Python 指数退避最大指数，不限制最终重试次数 |
| `RAG_KAFKA_PUBLISH_TIMEOUT_MS` | `3000` | Python 单条 Outbox 等待 Kafka 确认的最长时间，最小 100ms |
| `RAG_TASK_WORKER_ENABLED` | `true` | 是否启动 PostgreSQL 查询/local 索引耐久 worker |
| `RAG_TASK_WORKER_POLL_SECONDS` | `1` | durable worker 空闲轮询间隔 |
| `RAG_TASK_WORKER_BATCH_SIZE` | `4` | 每轮抢占的查询/local 索引任务数 |
| `RAG_TASK_WORKER_LEASE_SECONDS` | `120` | durable worker 任务租约时长 |
| `RAG_QUERY_TASK_TTL_SECONDS` | `1800` | 未完成查询任务转为 `EXPIRED` 的时间 |
