package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentMemoryVersion;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentMemoryVersionMapper {

    /**
     * 新增记忆版本关系。
     */
    void insert(AgentMemoryVersion version);

    /**
     * 查询当前用户某条记忆相关的版本关系。
     */
    List<AgentMemoryVersion> findByMemoryId(@Param("memoryId") String memoryId, @Param("userId") String userId);
}
