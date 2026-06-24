package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentMemoryAudit;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentMemoryAuditMapper {

    /**
     * 新增记忆生命周期审计。
     */
    void insert(AgentMemoryAudit audit);

    /**
     * 查询某条记忆的脱敏审计记录。
     */
    List<AgentMemoryAudit> findByMemoryId(@Param("memoryId") String memoryId, @Param("userId") String userId);
}
