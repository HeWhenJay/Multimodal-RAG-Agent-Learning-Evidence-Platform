package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.AgentMemoryCreateDTO;
import com.itxiang.evidence.dto.AgentMemoryPatchDTO;
import com.itxiang.evidence.entity.AgentTask;
import com.itxiang.evidence.vo.AgentMemoryDetailVO;
import com.itxiang.evidence.vo.AgentMemoryVO;

import java.util.List;
import java.util.Map;

public interface AgentMemoryService {

    /**
     * 创建当前用户显式授权的记忆。
     */
    AgentMemoryVO createMemory(AgentMemoryCreateDTO dto, String userId);

    /**
     * 查询当前用户记忆列表。
     */
    List<AgentMemoryVO> listMemories(String userId, String status, String memoryType, String namespace, String scopeType);

    /**
     * 查询当前用户单条记忆详情。
     */
    AgentMemoryDetailVO getMemory(String memoryId, String userId);

    /**
     * 用户确认待审记忆。
     */
    AgentMemoryVO confirmMemory(String memoryId, String userId);

    /**
     * 用户拒绝待审记忆。
     */
    AgentMemoryVO rejectMemory(String memoryId, String userId);

    /**
     * 修改当前用户记忆，内容或 scope 修改会生成新版本。
     */
    AgentMemoryVO patchMemory(String memoryId, AgentMemoryPatchDTO dto, String userId);

    /**
     * 用户归档记忆并停用索引。
     */
    AgentMemoryVO archiveMemory(String memoryId, String userId);

    /**
     * 用户删除记忆并擦除正文。
     */
    AgentMemoryVO deleteMemory(String memoryId, String userId);

    /**
     * 根据当前 Agent 任务检索可注入上下文的记忆。
     */
    List<Map<String, Object>> retrieveForTask(AgentTask task, Map<String, Object> arguments);

    /**
     * 根据当前 Agent 任务生成记忆候选，不落库。
     */
    Map<String, Object> proposeCandidates(AgentTask task, Map<String, Object> arguments);

    /**
     * 从任务草稿或最终输出中保存待确认记忆候选。
     */
    int savePendingCandidatesFromTask(AgentTask task);

    /**
     * 保存 Java Tool Gateway 收到的候选。
     */
    AgentMemoryVO saveCandidateFromTool(AgentTask task, Map<String, Object> arguments, boolean explicitRemember);
}
