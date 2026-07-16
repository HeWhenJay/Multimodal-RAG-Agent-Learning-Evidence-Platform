package com.itxiang.evidence.service;

/**
 * 协调 Agent 数据库事务与 Redis 上下文缓存的删除修复。
 */
public interface AgentCacheRepairService {

    /**
     * 在当前数据库事务中登记缓存删除，并在提交后立即尝试执行。
     */
    void requestAfterCommit(String userId, String taskId);

    /**
     * 处理到期的缓存删除修复任务。
     */
    void repairDueTasks();
}
