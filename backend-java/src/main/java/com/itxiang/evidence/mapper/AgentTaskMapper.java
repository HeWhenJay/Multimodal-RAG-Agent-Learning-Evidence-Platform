package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentTask;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface AgentTaskMapper {

    /**
     * 新增 Agent 任务。
     */
    void insert(AgentTask task);

    /**
     * 按任务 ID 查询任务。
     */
    AgentTask findById(@Param("id") String id);

    /**
     * 按当前用户查询任务详情。
     */
    AgentTask findByIdAndUserId(@Param("id") String id, @Param("userId") String userId);

    /**
     * 回写 Python Agent 任务事件后的主状态。
     */
    void updateFromEvent(AgentTask task);
}
