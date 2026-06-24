package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentToolCall;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentToolCallMapper {

    /**
     * 新增工具调用记录。
     */
    void insert(AgentToolCall toolCall);

    /**
     * 按 ID 查询工具调用记录。
     */
    AgentToolCall findById(@Param("id") String id);

    /**
     * 查询任务下的工具调用时间线。
     */
    List<AgentToolCall> findByTaskId(@Param("taskId") String taskId);

    /**
     * 回写工具调用执行结果。
     */
    void updateResult(AgentToolCall toolCall);
}
