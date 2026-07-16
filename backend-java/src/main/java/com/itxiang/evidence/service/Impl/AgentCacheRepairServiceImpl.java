package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.entity.AgentCacheRepairTask;
import com.itxiang.evidence.mapper.AgentCacheRepairTaskMapper;
import com.itxiang.evidence.service.AgentCacheRepairService;
import com.itxiang.evidence.service.AgentRuntimeStateAdapter;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

/**
 * Redis 删除失败时保留数据库修复记录，未修复期间读取路径绕过热缓存。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class AgentCacheRepairServiceImpl implements AgentCacheRepairService {

    private static final int BATCH_SIZE = 50;

    private final AgentCacheRepairTaskMapper agentCacheRepairTaskMapper;
    private final AgentRuntimeStateAdapter agentRuntimeStateAdapter;
    private final TransactionTemplate transactionTemplate;

    @Override
    public void requestAfterCommit(String userId, String taskId) {
        if (userId == null || userId.isBlank() || taskId == null || taskId.isBlank()) {
            return;
        }
        savePendingTask(userId, taskId);
        runAfterCommit(() -> repairTask(userId, taskId));
    }

    /**
     * 定时重试 Redis 上下文缓存删除，避免 Redis 恢复后继续读取旧热数据。
     */
    @Override
    @Scheduled(fixedDelayString = "${evidence.agent.redis.cache-repair-fixed-delay-ms:10000}")
    public void repairDueTasks() {
        List<AgentCacheRepairTask> tasks = agentCacheRepairTaskMapper.findDue(OffsetDateTime.now(ZoneOffset.UTC), BATCH_SIZE);
        for (AgentCacheRepairTask task : tasks) {
            repairTask(task.getUserId(), task.getTaskId());
        }
    }

    private void repairTask(String userId, String taskId) {
        try {
            transactionTemplate.executeWithoutResult(status -> {
                if (agentRuntimeStateAdapter.invalidateContext(userId, taskId)) {
                    agentCacheRepairTaskMapper.markResolved(taskId);
                    return;
                }
                markRetry(taskId, "Redis 上下文缓存删除未完成");
            });
        } catch (Exception e) {
            log.warn("Agent Redis 缓存删除修复失败: taskId={}, errorType={}", taskId, e.getClass().getSimpleName());
            transactionTemplate.executeWithoutResult(status -> markRetry(taskId, safeErrorSummary(e)));
        }
    }

    /**
     * PostgreSQL 与 H2 都兼容的任务登记；同一任务并发登记时由主键约束兜底。
     */
    private void savePendingTask(String userId, String taskId) {
        if (agentCacheRepairTaskMapper.exists(taskId)) {
            agentCacheRepairTaskMapper.resetPending(userId, taskId);
            return;
        }
        try {
            agentCacheRepairTaskMapper.insertPending(userId, taskId);
        } catch (DuplicateKeyException ignored) {
            agentCacheRepairTaskMapper.resetPending(userId, taskId);
        }
    }

    private void markRetry(String taskId, String errorMessage) {
        String safeMessage = errorMessage == null ? "Redis 上下文缓存删除失败" : errorMessage;
        agentCacheRepairTaskMapper.markRetry(taskId, truncate(safeMessage, 1000), OffsetDateTime.now(ZoneOffset.UTC).plusSeconds(10));
    }

    private String safeErrorSummary(Exception error) {
        return "Redis 上下文缓存删除失败：" + error.getClass().getSimpleName();
    }

    private void runAfterCommit(Runnable task) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()
                || !TransactionSynchronizationManager.isActualTransactionActive()) {
            task.run();
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                task.run();
            }
        });
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
