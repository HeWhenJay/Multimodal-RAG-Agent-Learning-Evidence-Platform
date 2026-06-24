package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.service.AgentService;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentTaskDetailVO;
import com.itxiang.evidence.vo.AgentTaskVO;
import com.itxiang.evidence.vo.AgentToolDefinitionVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.function.Supplier;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/agent")
@Tag(name = "Agent", description = "第二阶段 Agent 任务接口")
public class AgentController {

    private final AgentService agentService;
    private final AuthService authService;

    /**
     * 创建当前用户的 Agent 任务。
     */
    @PostMapping("/tasks")
    @Operation(summary = "创建 Agent 任务")
    public Result<AgentTaskVO> createTask(@Valid @RequestBody AgentTaskCreateDTO dto,
                                          @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("创建 Agent 任务: taskType={}, title={}", dto.getTaskType(), dto.getTitle());
        return execute(() -> agentService.createTask(dto, currentUserId(authorization)));
    }

    /**
     * 查询当前用户的 Agent 任务详情。
     */
    @GetMapping("/tasks/{taskId}")
    @Operation(summary = "查询 Agent 任务详情")
    public Result<AgentTaskDetailVO> getTask(@PathVariable String taskId,
                                             @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("查询 Agent 任务详情: taskId={}", taskId);
        return execute(() -> agentService.getTask(taskId, currentUserId(authorization)));
    }

    /**
     * 提交当前用户的 Agent 审批决策。
     */
    @PostMapping("/tasks/{taskId}/reviews/{reviewId}/decide")
    @Operation(summary = "提交 Agent 审批决策")
    public Result<AgentTaskDetailVO> decideReview(@PathVariable String taskId,
                                                  @PathVariable String reviewId,
                                                  @Valid @RequestBody AgentReviewDecisionDTO dto,
                                                  @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("提交 Agent 审批决策: taskId={}, reviewId={}, decision={}", taskId, reviewId, dto.getDecision());
        return execute(() -> agentService.decideReview(taskId, reviewId, dto, currentUserId(authorization)));
    }

    /**
     * 撤销当前用户窗口内的 Agent 变更操作。
     */
    @PostMapping("/operations/{operationId}/undo")
    @Operation(summary = "撤销 Agent 变更操作")
    public Result<AgentOperationVO> undoOperation(@PathVariable String operationId,
                                                 @Valid @RequestBody AgentOperationUndoDTO dto,
                                                 @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("撤销 Agent 变更操作: operationId={}", operationId);
        return execute(() -> agentService.undoOperation(operationId, dto, currentUserId(authorization)));
    }

    /**
     * 获取当前阶段开放的 Agent 工具能力。
     */
    @GetMapping("/tools")
    @Operation(summary = "获取 Agent 工具能力")
    public Result<List<AgentToolDefinitionVO>> listTools(@RequestHeader(value = "Authorization", required = false) String authorization) {
        return execute(() -> {
            currentUserId(authorization);
            return agentService.listTools();
        });
    }

    /**
     * 执行 Agent 控制器逻辑并转为统一 Result。
     */
    private <T> Result<T> execute(Supplier<T> supplier) {
        try {
            return Result.success(supplier.get());
        } catch (IllegalArgumentException e) {
            log.warn("Agent 请求失败: {}", e.getMessage());
            return Result.error("AGENT_VALIDATION_FAILED：" + e.getMessage());
        } catch (Exception e) {
            log.warn("Agent 请求异常: {}", e.getMessage());
            return Result.error("AGENT_UNEXPECTED_ERROR：" + e.getMessage());
        }
    }

    /**
     * 根据 Bearer Token 获取当前登录用户 ID。
     */
    private String currentUserId(String authorization) {
        return String.valueOf(authService.currentUser(bearerToken(authorization)).getId());
    }

    /**
     * 从 Authorization 头中提取 Bearer Token。
     */
    private String bearerToken(String authorization) {
        if (authorization == null || authorization.isBlank()) {
            return null;
        }
        String prefix = "Bearer ";
        return authorization.startsWith(prefix) ? authorization.substring(prefix.length()).trim() : authorization.trim();
    }
}
