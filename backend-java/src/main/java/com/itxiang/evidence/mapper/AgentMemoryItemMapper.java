package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentMemoryItem;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentMemoryItemMapper {

    /**
     * 新增 Agent 记忆条目。
     */
    void insert(AgentMemoryItem item);

    /**
     * 按 ID 查询记忆。
     */
    AgentMemoryItem findById(@Param("id") String id);

    /**
     * 按当前用户查询记忆。
     */
    AgentMemoryItem findByIdAndUserId(@Param("id") String id, @Param("userId") String userId);

    /**
     * 按来源哈希查询当前用户的记忆，用于候选去重。
     */
    AgentMemoryItem findByUserIdAndSourceHash(@Param("userId") String userId, @Param("sourceHash") String sourceHash);

    /**
     * 查询当前用户记忆列表。
     */
    List<AgentMemoryItem> findByUser(
            @Param("userId") String userId,
            @Param("status") String status,
            @Param("memoryType") String memoryType,
            @Param("namespace") String namespace,
            @Param("scopeType") String scopeType
    );

    /**
     * 查询当前用户待确认记忆。
     */
    List<AgentMemoryItem> findPendingReviewByUser(@Param("userId") String userId);

    /**
     * 查询当前用户可注入上下文的 ACTIVE 记忆。
     */
    List<AgentMemoryItem> findActiveByUser(@Param("userId") String userId);

    /**
     * 更新记忆条目的业务字段和生命周期状态。
     */
    void update(AgentMemoryItem item);

    /**
     * 标记记忆被访问，用于时近性和访问审计。
     */
    void markAccessed(@Param("id") String id, @Param("userId") String userId);
}
