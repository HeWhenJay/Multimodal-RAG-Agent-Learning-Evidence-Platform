package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentOperation;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentOperationMapper {

    /**
     * 新增 Agent 变更操作。
     */
    void insert(AgentOperation operation);

    /**
     * 按 ID 查询操作。
     */
    AgentOperation findById(@Param("id") String id);

    /**
     * 按任务查询操作。
     */
    List<AgentOperation> findByTaskId(@Param("taskId") String taskId);

    /**
     * 按幂等维度查询操作。
     */
    AgentOperation findByIdempotency(@Param("userId") String userId,
                                     @Param("operationType") String operationType,
                                     @Param("resourceType") String resourceType,
                                     @Param("resourceId") String resourceId,
                                     @Param("idempotencyKey") String idempotencyKey);

    /**
     * 更新操作执行结果。
     */
    void updateResult(AgentOperation operation);
}
