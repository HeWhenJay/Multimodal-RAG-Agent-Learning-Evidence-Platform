# Kafka 故障恢复与死信处理

## 适用范围

本文描述 RAG 索引、资料分片收尾和索引终态同步的 Kafka 可靠性约定。查询接口不依赖 Kafka，Kafka 不可用时仍走既有 HTTP 查询路径。

## 投递与恢复

- Java 在业务事务内同时写入资料状态、索引任务和 `rag_outbox_event`。事务成功后由 Outbox 发布 Kafka 消息。
- Broker 在任务提交前不可达时，且 `EVIDENCE_RAG_KAFKA_FALLBACK_ENABLED=true`，新任务直接回退到 HTTP / 本机异步索引。
- 已进入 Outbox 的任务不会改投 HTTP。Broker 暂时不可用时保留并指数退避重试；服务或 Broker 重启后，过期 `PUBLISHING` 租约会重新投递。
- Kafka 采用至少一次投递。Java 使用 `rag_consumed_event` 去重，Python 的 staging / promote 以 jobId、requestVersion 和幂等键防止旧消息覆盖新索引。

## 死信规则

- Python 索引请求遇到永久源文件错误，或重试次数耗尽时，会先发送失败结果，再发送脱敏后的 `RAG_INDEX_DLQ` 到 `rag.material.index.dlq.v1`。
- Java 消费端解析失败或业务异常会进行有限次本地重试；仍失败时发送 `RAG_KAFKA_CONSUMER_DLQ`。DLQ 消息及其 `log_error` 记录只保存资料 ID、job ID、源 topic、partition、offset、错误摘要和消息摘要哈希，不保存资料正文、Token 或对象存储密钥。
- Java 消费 DLQ 后会记录 `log_error`；日志以脱敏定位摘要区分不同死信，同一 Kafka 消息重复投递只增加出现次数。对索引和分片收尾等终态失败，会将对应 job / 资料标为 `FAILED` 或 `DLQ`，清除 active job，避免页面长期显示“解析中”。
- DLQ 不自动无限重放。修复源文件、权限或配置后，重新发起资料重建索引；这样会产生新的 jobId 和 requestVersion，避免旧消息覆盖已修复的资料。

## Redis 缓存一致性

- PostgreSQL 是 Agent 消息和摘要的唯一权威来源，Redis 只保存短期热态上下文和 SSE 缓冲。
- 事务中的消息或摘要写入会同时登记 `agent_cache_repair_task`，只在数据库提交后删除 Redis 上下文缓存。
- Redis 删除失败时，修复任务保留在数据库并定时重试；任务未修复前，读取路径绕过 Redis，直接从 PostgreSQL 重建上下文，因此不会读取旧缓存。

## 关键配置

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `EVIDENCE_RAG_KAFKA_FALLBACK_ENABLED` | `true` | 新任务提交前 Broker 不可达时是否回退 HTTP / 本机异步链路 |
| `EVIDENCE_RAG_KAFKA_HEALTH_CHECK_TIMEOUT_MS` | `1500` | Java 探测 Broker 的最大等待时间 |
| `EVIDENCE_RAG_KAFKA_PUBLISH_TIMEOUT_MS` | `3000` | Java 单条 Outbox / DLQ 投递等待时间 |
| `EVIDENCE_RAG_KAFKA_CONSUMER_MAX_ATTEMPTS` | `3` | Java 消费失败后的总尝试次数 |
| `EVIDENCE_RAG_KAFKA_CONSUMER_RETRY_DELAY_MS` | `1000` | Java 消费失败的本地重试间隔 |
| `RAG_KAFKA_RECONNECT_INITIAL_SECONDS` | `1` | Python worker 首次重连等待时间 |
| `RAG_KAFKA_RECONNECT_MAX_SECONDS` | `30` | Python worker 最大重连等待时间 |
| `RAG_KAFKA_PRODUCER_FLUSH_SECONDS` | `5` | Python producer 等待投递确认的最长时间 |
