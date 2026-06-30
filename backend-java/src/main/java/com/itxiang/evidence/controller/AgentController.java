package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.dto.AgentConversationFolderCreateDTO;
import com.itxiang.evidence.dto.AgentConversationFolderUpdateDTO;
import com.itxiang.evidence.dto.AgentConversationMoveDTO;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.service.AgentService;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.vo.AgentConversationFolderVO;
import com.itxiang.evidence.vo.AgentConversationTreeVO;
import com.itxiang.evidence.vo.AgentMessagePageVO;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentTaskDetailVO;
import com.itxiang.evidence.vo.AgentTaskVO;
import com.itxiang.evidence.vo.AgentToolDefinitionVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.function.Supplier;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/agent")
@Tag(name = "Agent", description = "Agent 任务接口")
public class AgentController {

    private final AgentService agentService;
    private final AuthService authService;
    private static final Set<String> TERMINAL_STATUSES = Set.of("COMPLETED", "FAILED", "CANCELED");

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
     * 查询当前用户最近的 Agent 会话任务。
     */
    @GetMapping("/tasks")
    @Operation(summary = "查询最近 Agent 会话任务")
    public Result<List<AgentTaskVO>> listTasks(@RequestParam(defaultValue = "20") Integer limit,
                                               @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("查询最近 Agent 会话任务: limit={}", limit);
        return execute(() -> agentService.listRecentTasks(currentUserId(authorization), limit));
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
     * 查询当前用户的 Agent 任务聊天消息记录。
     */
    @GetMapping("/tasks/{taskId}/messages")
    @Operation(summary = "查询 Agent 任务聊天消息")
    public Result<AgentMessagePageVO> listTaskMessages(@PathVariable String taskId,
                                                       @RequestParam(value = "beforeSequenceNo", required = false) Long beforeSequenceNo,
                                                       @RequestParam(value = "afterSequenceNo", required = false) Long afterSequenceNo,
                                                       @RequestParam(value = "limit", defaultValue = "30") Integer limit,
                                                       @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("分页查询 Agent 任务聊天消息: taskId={}, before={}, after={}, limit={}", taskId, beforeSequenceNo, afterSequenceNo, limit);
        return execute(() -> agentService.listTaskMessages(taskId, currentUserId(authorization), beforeSequenceNo, afterSequenceNo, limit));
    }

    /**
     * 查询侧边栏会话树，包含未分类和用户自定义文件夹。
     */
    @GetMapping("/conversations/tree")
    @Operation(summary = "查询 Agent 会话树")
    public Result<AgentConversationTreeVO> listConversationTree(@RequestParam(defaultValue = "8") Integer limitPerFolder,
                                                                @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("查询 Agent 会话树: limitPerFolder={}", limitPerFolder);
        return execute(() -> agentService.listConversationTree(currentUserId(authorization), limitPerFolder));
    }

    /**
     * 创建当前用户的会话文件夹。
     */
    @PostMapping("/conversation-folders")
    @Operation(summary = "创建 Agent 会话文件夹")
    public Result<AgentConversationFolderVO> createConversationFolder(@Valid @RequestBody AgentConversationFolderCreateDTO dto,
                                                                      @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("创建 Agent 会话文件夹: name={}", dto.getName());
        return execute(() -> agentService.createConversationFolder(dto, currentUserId(authorization)));
    }

    /**
     * 更新当前用户的会话文件夹。
     */
    @PutMapping("/conversation-folders/{folderId}")
    @Operation(summary = "更新 Agent 会话文件夹")
    public Result<AgentConversationFolderVO> updateConversationFolder(@PathVariable String folderId,
                                                                      @Valid @RequestBody AgentConversationFolderUpdateDTO dto,
                                                                      @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("更新 Agent 会话文件夹: folderId={}, name={}", folderId, dto.getName());
        return execute(() -> agentService.updateConversationFolder(folderId, dto, currentUserId(authorization)));
    }

    /**
     * 删除当前用户的会话文件夹，会话回到未分类。
     */
    @DeleteMapping("/conversation-folders/{folderId}")
    @Operation(summary = "删除 Agent 会话文件夹")
    public Result<Void> deleteConversationFolder(@PathVariable String folderId,
                                                 @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("删除 Agent 会话文件夹: folderId={}", folderId);
        return execute(() -> {
            agentService.deleteConversationFolder(folderId, currentUserId(authorization));
            return null;
        });
    }

    /**
     * 移动当前用户的会话到指定文件夹，folderId 为空表示未分类。
     */
    @PostMapping("/tasks/{taskId}/folder")
    @Operation(summary = "移动 Agent 会话文件夹")
    public Result<AgentTaskVO> moveConversation(@PathVariable String taskId,
                                                @RequestBody(required = false) AgentConversationMoveDTO dto,
                                                @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("移动 Agent 会话文件夹: taskId={}, folderId={}", taskId, dto == null ? null : dto.getFolderId());
        return execute(() -> agentService.moveConversation(taskId, dto, currentUserId(authorization)));
    }

    /**
     * 订阅当前 Agent 任务快照流，前端用于展示计划生成和工具观察进度。
     */
    @GetMapping(path = "/tasks/{taskId}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @Operation(summary = "订阅 Agent 任务事件流")
    public SseEmitter streamTask(@PathVariable String taskId,
                                 @RequestParam(value = "token", required = false) String token) {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException("登录状态已失效，无法订阅 Agent 事件流");
        }
        String userId = currentUserId("Bearer " + token);
        SseEmitter emitter = new SseEmitter(120_000L);
        emitter.onTimeout(emitter::complete);
        emitter.onCompletion(() -> log.debug("Agent 任务事件流已关闭: taskId={}", taskId));
        CompletableFuture.runAsync(() -> streamTaskSnapshots(taskId, userId, emitter));
        return emitter;
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
     * 执行 Agent 控制器逻辑并转换为统一 Result。
     */
    private <T> Result<T> execute(Supplier<T> supplier) {
        try {
            return Result.success(supplier.get());
        } catch (IllegalArgumentException e) {
            log.warn("Agent 请求失败: {}", e.getMessage());
            return Result.error("AGENT_VALIDATION_FAILED: " + e.getMessage());
        } catch (Exception e) {
            log.warn("Agent 请求异常: {}", e.getMessage());
            return Result.error("AGENT_UNEXPECTED_ERROR: " + e.getMessage());
        }
    }

    /**
     * 以短轮询方式向 SSE 发送任务快照，避免创建任务接口阻塞等待 Python。
     */
    private void streamTaskSnapshots(String taskId, String userId, SseEmitter emitter) {
        try {
            String lastFingerprint = "";
            long lastBufferedSequence = 0L;
            List<Map<String, Object>> bufferedEvents = agentService.listTaskStreamEvents(taskId, userId);
            for (Map<String, Object> event : bufferedEvents) {
                emitter.send(SseEmitter.event().name("agent_event").data(event));
            }
            for (Map<String, Object> event : bufferedEvents) {
                lastBufferedSequence = Math.max(lastBufferedSequence, bufferSequence(event));
            }
            for (int index = 0; index < 120; index++) {
                List<Map<String, Object>> events = agentService.listTaskStreamEvents(taskId, userId);
                List<Map<String, Object>> newEvents = new java.util.ArrayList<>();
                for (Map<String, Object> event : events) {
                    if (bufferSequence(event) > lastBufferedSequence) {
                        newEvents.add(event);
                    }
                }
                if (!newEvents.isEmpty()) {
                    for (Map<String, Object> event : newEvents) {
                        emitter.send(SseEmitter.event().name("agent_event").data(event));
                    }
                    for (Map<String, Object> event : newEvents) {
                        lastBufferedSequence = Math.max(lastBufferedSequence, bufferSequence(event));
                    }
                }
                AgentTaskDetailVO detail = agentService.getTask(taskId, userId);
                String fingerprint = detail.getStatus()
                        + "|" + detail.getUpdatedAt()
                        + "|" + detail.getToolCalls().size()
                        + "|" + detail.getReviews().size()
                        + "|" + detail.getOperations().size()
                        + "|" + detail.getMessages().size()
                        + "|" + detail.getErrorCode()
                        + "|" + detail.getErrorMessage();
                if (!fingerprint.equals(lastFingerprint) || index == 0) {
                    emitter.send(SseEmitter.event().name("task").data(detail));
                    lastFingerprint = fingerprint;
                }
                if (TERMINAL_STATUSES.contains(detail.getStatus())) {
                    emitter.send(SseEmitter.event().name("done").data(detail));
                    emitter.complete();
                    return;
                }
                Thread.sleep(1000L);
            }
            emitter.complete();
        } catch (Exception e) {
            log.warn("Agent 任务事件流结束: taskId={}, message={}", taskId, e.getMessage());
            emitter.completeWithError(e);
        }
    }

    /**
     * 读取 Redis SSE 缓冲序号；旧缓存没有该字段时返回 0，继续靠快照兜底。
     */
    private long bufferSequence(Map<String, Object> event) {
        if (event == null) {
            return 0L;
        }
        Object value = event.get("bufferSequence");
        if (value instanceof Number number) {
            return number.longValue();
        }
        try {
            return value == null ? 0L : Long.parseLong(String.valueOf(value));
        } catch (Exception e) {
            return 0L;
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
