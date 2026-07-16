package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

/**
 * 记录数据库提交后未完成的 Agent Redis 上下文缓存删除任务。
 */
@Data
public class AgentCacheRepairTask {

    private String taskId;
    private String userId;
    private String status;
    private Integer attempt;
    private OffsetDateTime nextAttemptAt;
    private String lastError;
    private OffsetDateTime resolvedAt;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
