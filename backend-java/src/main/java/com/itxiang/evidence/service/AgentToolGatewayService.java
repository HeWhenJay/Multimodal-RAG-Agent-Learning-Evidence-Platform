package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.AgentReadToolRequestDTO;
import com.itxiang.evidence.dto.AgentMutationToolExecuteDTO;
import com.itxiang.evidence.vo.AgentToolResultVO;

public interface AgentToolGatewayService {

    /**
     * 执行只读 Agent 工具。
     */
    AgentToolResultVO executeReadTool(AgentReadToolRequestDTO request);

    /**
     * 执行已审批的变更 Agent 工具。
     */
    AgentToolResultVO executeMutationTool(AgentMutationToolExecuteDTO request);
}
