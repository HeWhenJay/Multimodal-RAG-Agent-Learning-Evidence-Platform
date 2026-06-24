package com.itxiang.evidence;

import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.controller.AgentInternalController;
import com.itxiang.evidence.dto.AgentMutationToolExecuteDTO;
import com.itxiang.evidence.dto.AgentReadToolRequestDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.service.AgentService;
import com.itxiang.evidence.service.AgentToolGatewayService;
import com.itxiang.evidence.vo.AgentToolResultVO;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.Map;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class AgentInternalControllerTests {

    private AgentToolGatewayService agentToolGatewayService;
    private AgentService agentService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        agentToolGatewayService = mock(AgentToolGatewayService.class);
        agentService = mock(AgentService.class);
        AgentProperties properties = new AgentProperties();
        properties.setInternalToken("agent-secret");
        mockMvc = MockMvcBuilders.standaloneSetup(new AgentInternalController(agentToolGatewayService, agentService, properties)).build();
    }

    @Test
    void missingTokenReturnsStructuredUnauthorizedAndDoesNotCallService() throws Exception {
        mockMvc.perform(post("/api/internal/agent/tools/read")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(readToolJson()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.status").value("REJECTED"))
                .andExpect(jsonPath("$.errorCode").value("AGENT_INTERNAL_TOKEN_INVALID"));

        verify(agentToolGatewayService, never()).executeReadTool(any());
    }

    @Test
    void wrongTokenReturnsStructuredUnauthorizedAndDoesNotCallService() throws Exception {
        mockMvc.perform(post("/api/internal/agent/tools/read")
                        .header("X-Agent-Internal-Token", "bad-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(readToolJson()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.errorCode").value("AGENT_INTERNAL_TOKEN_INVALID"));

        verify(agentToolGatewayService, never()).executeReadTool(any());
    }

    @Test
    void validTokenDelegatesToGateway() throws Exception {
        when(agentToolGatewayService.executeReadTool(any(AgentReadToolRequestDTO.class))).thenReturn(
                AgentToolResultVO.builder()
                        .taskId("agent-task-1")
                        .toolCallId("tool-call-1")
                        .toolName("material_status_reader")
                        .status("SUCCEEDED")
                        .ownershipVerified(true)
                        .scope("current_user_or_authorized")
                        .build()
        );

        mockMvc.perform(post("/api/internal/agent/tools/read")
                        .header("X-Agent-Internal-Token", "agent-secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(readToolJson()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("SUCCEEDED"))
                .andExpect(jsonPath("$.ownershipVerified").value(true));

        verify(agentToolGatewayService).executeReadTool(any(AgentReadToolRequestDTO.class));
    }

    @Test
    void mutationMissingTokenReturnsStructuredUnauthorizedAndDoesNotCallService() throws Exception {
        mockMvc.perform(post("/api/internal/agent/tools/mutation/execute")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(mutationToolJson()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.status").value("REJECTED"))
                .andExpect(jsonPath("$.errorCode").value("AGENT_INTERNAL_TOKEN_INVALID"));

        verify(agentToolGatewayService, never()).executeMutationTool(any());
    }

    @Test
    void validMutationTokenDelegatesToGateway() throws Exception {
        when(agentToolGatewayService.executeMutationTool(any(AgentMutationToolExecuteDTO.class))).thenReturn(
                AgentToolResultVO.builder()
                        .taskId("agent-task-1")
                        .toolCallId("tool-call-mutation-1")
                        .toolName("jd_learning_plan_save")
                        .status("SUCCEEDED")
                        .ownershipVerified(true)
                        .scope("current_user_or_authorized")
                        .build()
        );

        mockMvc.perform(post("/api/internal/agent/tools/mutation/execute")
                        .header("X-Agent-Internal-Token", "agent-secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(mutationToolJson()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("SUCCEEDED"))
                .andExpect(jsonPath("$.ownershipVerified").value(true));

        verify(agentToolGatewayService).executeMutationTool(any(AgentMutationToolExecuteDTO.class));
    }

    @Test
    void eventMissingTokenReturnsStructuredUnauthorizedAndDoesNotCallService() throws Exception {
        mockMvc.perform(post("/api/internal/agent/tasks/agent-task-1/events")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(taskEventJson()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.accepted").value(false))
                .andExpect(jsonPath("$.errorCode").value("AGENT_INTERNAL_TOKEN_INVALID"));

        verify(agentService, never()).handleEvent(any(), any());
    }

    @Test
    void validEventTokenDelegatesToAgentService() throws Exception {
        when(agentService.handleEvent(eq("agent-task-1"), any(AgentTaskEventDTO.class)))
                .thenReturn(Map.of("taskId", "agent-task-1", "accepted", true, "status", "COMPLETED"));

        mockMvc.perform(post("/api/internal/agent/tasks/agent-task-1/events")
                        .header("X-Agent-Internal-Token", "agent-secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(taskEventJson()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(true))
                .andExpect(jsonPath("$.status").value("COMPLETED"));

        verify(agentService).handleEvent(eq("agent-task-1"), any(AgentTaskEventDTO.class));
    }

    /**
     * 构造只读工具请求 JSON。
     */
    private String readToolJson() {
        return """
                {
                  "taskId": "agent-task-1",
                  "toolCallId": "tool-call-1",
                  "toolName": "material_status_reader",
                  "arguments": {
                    "materialId": 12
                  }
                }
                """;
    }

    /**
     * 构造变更工具请求 JSON。
     */
    private String mutationToolJson() {
        return """
                {
                  "taskId": "agent-task-1",
                  "toolCallId": "tool-call-mutation-1",
                  "approvalId": "review-crud-1",
                  "toolName": "jd_learning_plan_save",
                  "idempotencyKey": "save-plan-agent-task-1-v1",
                  "arguments": {
                    "reason": "用户确认保存学习计划"
                  }
                }
                """;
    }

    /**
     * 构造任务事件请求 JSON。
     */
    private String taskEventJson() {
        return """
                {
                  "eventType": "TASK_COMPLETED",
                  "status": "COMPLETED",
                  "pythonThreadId": "agent-task-1",
                  "final": {
                    "answer": "已完成只读检索",
                    "evidenceIds": ["material-12-1"]
                  }
                }
                """;
    }
}
