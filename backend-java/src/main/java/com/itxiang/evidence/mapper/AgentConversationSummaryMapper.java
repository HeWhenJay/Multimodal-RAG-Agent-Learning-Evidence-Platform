package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentConversationSummary;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentConversationSummaryMapper {

    /**
     * 新增压缩摘要段。
     */
    void insert(AgentConversationSummary summary);

    /**
     * 保存新 ACTIVE 摘要前，将旧摘要标记为已替代。
     */
    void supersedeActiveByTask(@Param("taskId") String taskId, @Param("userId") String userId);

    /**
     * 查询任务最近摘要，供任务详情展示。
     */
    List<AgentConversationSummary> findByTaskId(@Param("taskId") String taskId);

    /**
     * 查询任务最近摘要窗口，避免详情接口全量返回长会话摘要。
     */
    List<AgentConversationSummary> findRecentByTaskId(@Param("taskId") String taskId, @Param("limit") Integer limit);

    /**
     * 统计任务摘要数量。
     */
    int countByTaskId(@Param("taskId") String taskId);

    /**
     * 查询任务指定状态摘要。
     */
    List<AgentConversationSummary> findByTaskIdAndStatus(@Param("taskId") String taskId,
                                                         @Param("status") String status,
                                                         @Param("limit") Integer limit);

    /**
     * 按摘要 ID 查询当前任务摘要。
     */
    AgentConversationSummary findByIdAndTask(@Param("id") String id, @Param("taskId") String taskId);

    /**
     * 查询当前任务最新已覆盖消息端点，用于定位未压缩候选窗口。
     */
    AgentConversationSummary findLatestCoveredByTask(@Param("taskId") String taskId);
}
