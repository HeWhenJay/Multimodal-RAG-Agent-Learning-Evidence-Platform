package com.itxiang.evidence.controller;

import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.dto.AgentConversationSummarySaveDTO;
import com.itxiang.evidence.dto.AgentMutationToolExecuteDTO;
import com.itxiang.evidence.dto.AgentReadToolRequestDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.service.AgentService;
import com.itxiang.evidence.service.AgentToolGatewayService;
import com.itxiang.evidence.vo.AgentChatMessageVO;
import com.itxiang.evidence.vo.AgentContextRestoreVO;
import com.itxiang.evidence.vo.AgentConversationSummaryVO;
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
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
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
        log.info("接收 Agent 内部只读工具调用: taskId={}, toolCallId={}, toolName={}",
                request.getTaskId(), request.getToolCallId(), request.getToolName());
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
        log.info("接收 Agent 内部变更工具调用: taskId={}, toolCallId={}, toolName={}",
                request.getTaskId(), request.getToolCallId(), request.getToolName());
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
        log.info("接收 Agent 内部任务事件: taskId={}, eventType={}, status={}",
                taskId,
                event == null ? null : event.getEventType(),
                event == null ? null : event.getStatus());
        return ResponseEntity.ok(agentService.handleEvent(taskId, event));
    }

    /**
     * 恢复当前任务上下文，Python 只能通过该接口读取消息窗口和摘要段。
     */
    @GetMapping("/tasks/{taskId}/context")
    @Operation(summary = "恢复 Agent 上下文")
    public ResponseEntity<?> restoreContext(
            @PathVariable String taskId,
            @RequestHeader(value = "X-Agent-Internal-Token", required = false) String token,
            @RequestParam(value = "query", required = false) String query,
            @RequestParam(value = "recentLimit", required = false) Integer recentLimit,
            @RequestParam(value = "summaryLimit", required = false) Integer summaryLimit,
            @RequestParam(value = "bestWindowTokens", required = false) Integer bestWindowTokens) {
        if (!internalTokenValid(token)) {
            log.warn("拒绝 Agent 内部上下文恢复: taskId={}", taskId);
            return internalTokenError(taskId);
        }
        AgentContextRestoreVO context = agentService.restoreContext(taskId, query, recentLimit, summaryLimit, bestWindowTokens);
        return ResponseEntity.ok(context);
    }

    /**
     * 保存上下文压缩摘要段到 PostgreSQL。
     */
    @PostMapping("/tasks/{taskId}/summaries")
    @Operation(summary = "保存 Agent 上下文压缩摘要")
    public ResponseEntity<?> saveConversationSummary(
            @PathVariable String taskId,
            @RequestHeader(value = "X-Agent-Internal-Token", required = false) String token,
            @RequestBody AgentConversationSummarySaveDTO request) {
        if (!internalTokenValid(token)) {
            log.warn("拒绝 Agent 内部摘要保存: taskId={}", taskId);
            return internalTokenError(taskId);
        }
        AgentConversationSummaryVO summary = agentService.saveConversationSummary(taskId, request);
        return ResponseEntity.ok(summary);
    }

    /**
     * 按摘要覆盖范围或消息锚点回捞少量原文。
     */
    @GetMapping("/tasks/{taskId}/context/messages")
    @Operation(summary = "回捞 Agent 摘要范围附近原文")
    public ResponseEntity<?> recallContextMessages(
            @PathVariable String taskId,
            @RequestHeader(value = "X-Agent-Internal-Token", required = false) String token,
            @RequestParam(value = "summaryId", required = false) String summaryId,
            @RequestParam(value = "coveredMessageStartId", required = false) String coveredMessageStartId,
            @RequestParam(value = "coveredMessageEndId", required = false) String coveredMessageEndId,
            @RequestParam(value = "anchorMessageId", required = false) String anchorMessageId,
            @RequestParam(value = "before", required = false) Integer before,
            @RequestParam(value = "after", required = false) Integer after,
            @RequestParam(value = "limit", required = false) Integer limit) {
        if (!internalTokenValid(token)) {
            log.warn("拒绝 Agent 内部上下文消息回捞: taskId={}", taskId);
            return internalTokenError(taskId);
        }
        List<AgentChatMessageVO> messages = agentService.recallContextMessages(
                taskId,
                summaryId,
                coveredMessageStartId,
                coveredMessageEndId,
                anchorMessageId,
                before,
                after,
                limit
        );
        return ResponseEntity.ok(messages);
    }

    /**
     * Agent 内部令牌必须显式配置且完全匹配。
     */
    private boolean internalTokenValid(String token) {
        String configured = agentProperties.getInternalToken();
        return configured != null && !configured.isBlank() && configured.equals(token);
    }

    private ResponseEntity<Map<String, Object>> internalTokenError(String taskId) {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .body(Map.of(
                        "taskId", taskId,
                        "accepted", false,
                        "errorCode", "AGENT_INTERNAL_TOKEN_INVALID",
                        "errorMessage", "内部 Agent 令牌无效"
                ));
    }
}
