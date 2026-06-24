package com.itxiang.evidence.controller;

import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.dto.AgentMutationToolExecuteDTO;
import com.itxiang.evidence.dto.AgentReadToolRequestDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.service.AgentService;
import com.itxiang.evidence.service.AgentToolGatewayService;
import com.itxiang.evidence.vo.AgentToolResultVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/internal/agent")
@Tag(name = "Agent 内部接口", description = "Python Agent 调 Java 的内部接口")
public class AgentInternalController {

    private final AgentToolGatewayService agentToolGatewayService;
    private final AgentService agentService;
    private final AgentProperties agentProperties;

    /**
     * 执行只读工具，内部 token 未配置、缺失或错误都拒绝。
     */
    @PostMapping("/tools/read")
    @Operation(summary = "执行 Agent 只读工具")
    public ResponseEntity<AgentToolResultVO> executeReadTool(
            @RequestHeader(value = "X-Agent-Internal-Token", required = false) String token,
            @Valid @RequestBody AgentReadToolRequestDTO request) {
        if (!internalTokenValid(token)) {
            log.warn("拒绝 Agent 内部只读工具调用: taskId={}, toolName={}",
                    request == null ? null : request.getTaskId(),
                    request == null ? null : request.getToolName());
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(AgentToolResultVO.failed(
                            request == null ? null : request.getTaskId(),
                            request == null ? null : request.getToolCallId(),
                            request == null ? null : request.getToolName(),
                            "AGENT_INTERNAL_TOKEN_INVALID",
                            "内部 Agent 令牌无效",
                            false
                    ));
        }
        return ResponseEntity.ok(agentToolGatewayService.executeReadTool(request));
    }

    /**
     * 执行已审批的变更工具，内部 token 未配置、缺失或错误都拒绝。
     */
    @PostMapping("/tools/mutation/execute")
    @Operation(summary = "执行 Agent 已审批变更工具")
    public ResponseEntity<AgentToolResultVO> executeMutationTool(
            @RequestHeader(value = "X-Agent-Internal-Token", required = false) String token,
            @Valid @RequestBody AgentMutationToolExecuteDTO request) {
        if (!internalTokenValid(token)) {
            log.warn("拒绝 Agent 内部变更工具调用: taskId={}, toolName={}",
                    request == null ? null : request.getTaskId(),
                    request == null ? null : request.getToolName());
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(AgentToolResultVO.failed(
                            request == null ? null : request.getTaskId(),
                            request == null ? null : request.getToolCallId(),
                            request == null ? null : request.getToolName(),
                            "AGENT_INTERNAL_TOKEN_INVALID",
                            "内部 Agent 令牌无效",
                            false
                    ));
        }
        return ResponseEntity.ok(agentToolGatewayService.executeMutationTool(request));
    }

    /**
     * 接收 Python Agent 回写的任务状态、工具观察和最终结果。
     */
    @PostMapping("/tasks/{taskId}/events")
    @Operation(summary = "接收 Agent 任务事件")
    public ResponseEntity<Map<String, Object>> handleTaskEvent(
            @PathVariable String taskId,
            @RequestHeader(value = "X-Agent-Internal-Token", required = false) String token,
            @RequestBody AgentTaskEventDTO event) {
        if (!internalTokenValid(token)) {
            log.warn("拒绝 Agent 内部任务事件: taskId={}", taskId);
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of(
                            "taskId", taskId,
                            "accepted", false,
                            "errorCode", "AGENT_INTERNAL_TOKEN_INVALID",
                            "errorMessage", "内部 Agent 令牌无效"
                    ));
        }
        return ResponseEntity.ok(agentService.handleEvent(taskId, event));
    }

    /**
     * Agent 内部令牌必须显式配置且完全匹配。
     */
    private boolean internalTokenValid(String token) {
        String configured = agentProperties.getInternalToken();
        return configured != null && !configured.isBlank() && configured.equals(token);
    }
}
