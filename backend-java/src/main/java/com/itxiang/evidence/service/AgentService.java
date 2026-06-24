package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentTaskDetailVO;
import com.itxiang.evidence.vo.AgentTaskVO;
import com.itxiang.evidence.vo.AgentToolDefinitionVO;

import java.util.List;
import java.util.Map;

public interface AgentService {

    /**
     * 创建当前用户的 Agent 任务。
     */
    AgentTaskVO createTask(AgentTaskCreateDTO dto, String userId);

    /**
     * 查询当前用户的 Agent 任务详情。
     */
    AgentTaskDetailVO getTask(String taskId, String userId);

    /**
     * 查询前端可展示的工具能力。
     */
    List<AgentToolDefinitionVO> listTools();

    /**
     * 处理 Python Agent 回写的任务事件。
     */
    Map<String, Object> handleEvent(String taskId, AgentTaskEventDTO event);

    /**
     * 处理当前用户提交的人工审批决策。
     */
    AgentTaskDetailVO decideReview(String taskId, String reviewId, AgentReviewDecisionDTO dto, String userId);

    /**
     * 撤销当前用户窗口内的 Agent 变更操作。
     */
    AgentOperationVO undoOperation(String operationId, AgentOperationUndoDTO dto, String userId);
}
