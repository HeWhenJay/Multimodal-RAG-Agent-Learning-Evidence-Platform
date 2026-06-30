package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.AgentConversationFolderCreateDTO;
import com.itxiang.evidence.dto.AgentConversationFolderUpdateDTO;
import com.itxiang.evidence.dto.AgentConversationMoveDTO;
import com.itxiang.evidence.dto.AgentConversationSummarySaveDTO;
import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.vo.AgentChatMessageVO;
import com.itxiang.evidence.vo.AgentConversationFolderVO;
import com.itxiang.evidence.vo.AgentConversationTreeVO;
import com.itxiang.evidence.vo.AgentContextRestoreVO;
import com.itxiang.evidence.vo.AgentMessagePageVO;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentConversationSummaryVO;
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
     * 查询当前用户最近的 Agent 会话任务。
     */
    List<AgentTaskVO> listRecentTasks(String userId, Integer limit);

    /**
     * 查询当前用户某个任务的聊天消息记录。
     */
    AgentMessagePageVO listTaskMessages(String taskId,
                                        String userId,
                                        Long beforeSequenceNo,
                                        Long afterSequenceNo,
                                        Integer limit);

    /**
     * 读取当前任务 SSE 重连事件缓冲。
     */
    List<Map<String, Object>> listTaskStreamEvents(String taskId, String userId);

    /**
     * 查询当前用户侧边栏会话树。
     */
    AgentConversationTreeVO listConversationTree(String userId, Integer limitPerFolder);

    /**
     * 创建当前用户会话文件夹。
     */
    AgentConversationFolderVO createConversationFolder(AgentConversationFolderCreateDTO dto, String userId);

    /**
     * 更新当前用户会话文件夹。
     */
    AgentConversationFolderVO updateConversationFolder(String folderId, AgentConversationFolderUpdateDTO dto, String userId);

    /**
     * 删除当前用户会话文件夹，并将其中会话移回未分类。
     */
    void deleteConversationFolder(String folderId, String userId);

    /**
     * 移动当前用户会话到指定文件夹。
     */
    AgentTaskVO moveConversation(String taskId, AgentConversationMoveDTO dto, String userId);

    /**
     * 查询前端可展示的工具能力。
     */
    List<AgentToolDefinitionVO> listTools();

    /**
     * 处理 Python Agent 回写的任务事件。
     */
    Map<String, Object> handleEvent(String taskId, AgentTaskEventDTO event);

    /**
     * Python Agent 通过 Java 权限边界恢复上下文，不直接读取业务库。
     */
    AgentContextRestoreVO restoreContext(String taskId,
                                         String query,
                                         Integer recentLimit,
                                         Integer summaryLimit,
                                         Integer bestWindowTokens);

    /**
     * 保存上下文压缩摘要段，并版本化替代旧 ACTIVE 摘要。
     */
    AgentConversationSummaryVO saveConversationSummary(String taskId, AgentConversationSummarySaveDTO dto);

    /**
     * 按摘要或消息锚点回捞覆盖范围附近少量原文。
     */
    List<AgentChatMessageVO> recallContextMessages(String taskId,
                                                   String summaryId,
                                                   String coveredMessageStartId,
                                                   String coveredMessageEndId,
                                                   String anchorMessageId,
                                                   Integer before,
                                                   Integer after,
                                                   Integer limit);

    /**
     * 处理当前用户提交的人工审批决策。
     */
    AgentTaskDetailVO decideReview(String taskId, String reviewId, AgentReviewDecisionDTO dto, String userId);

    /**
     * 撤销当前用户窗口内的 Agent 变更操作。
     */
    AgentOperationVO undoOperation(String operationId, AgentOperationUndoDTO dto, String userId);
}
