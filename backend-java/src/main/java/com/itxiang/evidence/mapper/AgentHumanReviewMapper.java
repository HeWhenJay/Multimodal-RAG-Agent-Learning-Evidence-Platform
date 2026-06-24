package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentHumanReview;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentHumanReviewMapper {

    /**
     * 新增人工审批记录。
     */
    void insert(AgentHumanReview review);

    /**
     * 按 ID 查询审批记录。
     */
    AgentHumanReview findById(@Param("id") String id);

    /**
     * 按任务查询审批记录。
     */
    List<AgentHumanReview> findByTaskId(@Param("taskId") String taskId);

    /**
     * 回写审批决策。
     */
    void updateDecision(AgentHumanReview review);
}
