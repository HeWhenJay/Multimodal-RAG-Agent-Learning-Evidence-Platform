package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.RagQueryHistory;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface RagQueryHistoryMapper {

    /**
     * 新增一条 RAG 询问历史。
     */
    void insert(RagQueryHistory history);

    /**
     * 根据任务 ID 更新 RAG 询问历史。
     */
    void updateByTaskId(RagQueryHistory history);

    /**
     * 按当前用户、日期范围和条数查询最近询问。
     */
    List<RagQueryHistory> findRecentByUserIdBetween(@Param("userId") String userId,
                                                    @Param("startTime") LocalDateTime startTime,
                                                    @Param("endTime") LocalDateTime endTime,
                                                    @Param("limit") Integer limit);

    /**
     * 根据任务 ID 查询历史记录。
     */
    RagQueryHistory findByTaskIdAndUserId(@Param("taskId") String taskId, @Param("userId") String userId);
}
