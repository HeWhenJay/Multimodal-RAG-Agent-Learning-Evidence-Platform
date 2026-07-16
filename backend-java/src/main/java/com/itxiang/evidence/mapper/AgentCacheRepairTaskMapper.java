package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentCacheRepairTask;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.OffsetDateTime;
import java.util.List;

@Mapper
public interface AgentCacheRepairTaskMapper {

    /**
     * 新增上下文缓存删除任务。
     */
    int insertPending(@Param("userId") String userId, @Param("taskId") String taskId);

    /**
     * 将已存在的缓存删除任务重置为最新待处理状态。
     */
    int resetPending(@Param("userId") String userId, @Param("taskId") String taskId);

    /**
     * 判断任务是否已有缓存删除记录，包含已完成记录。
     */
    boolean exists(@Param("taskId") String taskId);

    /**
     * 判断任务是否仍有待完成的缓存删除。
     */
    boolean hasPending(@Param("taskId") String taskId);

    /**
     * 查询到期的缓存删除修复任务。
     */
    List<AgentCacheRepairTask> findDue(@Param("now") OffsetDateTime now, @Param("limit") Integer limit);

    /**
     * 标记缓存删除已完成。
     */
    int markResolved(@Param("taskId") String taskId);

    /**
     * 记录删除失败并安排下一次尝试。
     */
    int markRetry(@Param("taskId") String taskId,
                  @Param("lastError") String lastError,
                  @Param("nextAttemptAt") OffsetDateTime nextAttemptAt);
}
