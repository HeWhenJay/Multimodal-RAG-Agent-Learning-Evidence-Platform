package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.dto.AgentMemoryCreateDTO;
import com.itxiang.evidence.dto.AgentMemoryPatchDTO;
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.vo.AgentMemoryDetailVO;
import com.itxiang.evidence.vo.AgentMemoryVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.function.Supplier;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/agent/memories")
@Tag(name = "Agent 记忆", description = "当前用户 Agent 记忆管理接口")
public class AgentMemoryController {

    private final AgentMemoryService agentMemoryService;
    private final AuthService authService;

    /**
     * 创建当前用户显式授权的 Agent 记忆。
     */
    @PostMapping
    @Operation(summary = "创建 Agent 记忆")
    public Result<AgentMemoryVO> createMemory(@Valid @RequestBody AgentMemoryCreateDTO dto,
                                              @RequestHeader(value = "Authorization", required = false) String authorization) {
        return execute(() -> agentMemoryService.createMemory(dto, currentUserId(authorization)));
    }

    /**
     * 查询当前用户的 Agent 记忆列表。
     */
    @GetMapping
    @Operation(summary = "查询 Agent 记忆列表")
    public Result<List<AgentMemoryVO>> listMemories(@RequestParam(required = false) String status,
                                                    @RequestParam(required = false) String memoryType,
                                                    @RequestParam(required = false) String namespace,
                                                    @RequestParam(required = false) String scopeType,
                                                    @RequestHeader(value = "Authorization", required = false) String authorization) {
        return execute(() -> agentMemoryService.listMemories(currentUserId(authorization), status, memoryType, namespace, scopeType));
    }

    /**
     * 查询单条 Agent 记忆详情。
     */
    @GetMapping("/{memoryId}")
    @Operation(summary = "查询 Agent 记忆详情")
    public Result<AgentMemoryDetailVO> getMemory(@PathVariable String memoryId,
                                                 @RequestHeader(value = "Authorization", required = false) String authorization) {
        return execute(() -> agentMemoryService.getMemory(memoryId, currentUserId(authorization)));
    }

    /**
     * 确认待审记忆并触发索引。
     */
    @PostMapping("/{memoryId}/confirm")
    @Operation(summary = "确认 Agent 记忆")
    public Result<AgentMemoryVO> confirmMemory(@PathVariable String memoryId,
                                               @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("确认 Agent 记忆: memoryId={}", memoryId);
        return execute(() -> agentMemoryService.confirmMemory(memoryId, currentUserId(authorization)));
    }

    /**
     * 拒绝待审记忆。
     */
    @PostMapping("/{memoryId}/reject")
    @Operation(summary = "拒绝 Agent 记忆")
    public Result<AgentMemoryVO> rejectMemory(@PathVariable String memoryId,
                                              @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("拒绝 Agent 记忆: memoryId={}", memoryId);
        return execute(() -> agentMemoryService.rejectMemory(memoryId, currentUserId(authorization)));
    }

    /**
     * 修改记忆内容或收窄作用域。
     */
    @PatchMapping("/{memoryId}")
    @Operation(summary = "修改 Agent 记忆")
    public Result<AgentMemoryVO> patchMemory(@PathVariable String memoryId,
                                             @RequestBody AgentMemoryPatchDTO dto,
                                             @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("修改 Agent 记忆: memoryId={}", memoryId);
        return execute(() -> agentMemoryService.patchMemory(memoryId, dto, currentUserId(authorization)));
    }

    /**
     * 归档当前用户记忆。
     */
    @PostMapping("/{memoryId}/archive")
    @Operation(summary = "归档 Agent 记忆")
    public Result<AgentMemoryVO> archiveMemory(@PathVariable String memoryId,
                                               @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("归档 Agent 记忆: memoryId={}", memoryId);
        return execute(() -> agentMemoryService.archiveMemory(memoryId, currentUserId(authorization)));
    }

    /**
     * 删除当前用户记忆。
     */
    @DeleteMapping("/{memoryId}")
    @Operation(summary = "删除 Agent 记忆")
    public Result<AgentMemoryVO> deleteMemory(@PathVariable String memoryId,
                                              @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("删除 Agent 记忆: memoryId={}", memoryId);
        return execute(() -> agentMemoryService.deleteMemory(memoryId, currentUserId(authorization)));
    }

    /**
     * 执行业务逻辑并转为统一 Result。
     */
    private <T> Result<T> execute(Supplier<T> supplier) {
        try {
            return Result.success(supplier.get());
        } catch (IllegalArgumentException e) {
            log.warn("Agent 记忆请求失败: {}", e.getMessage());
            return Result.error(e.getMessage());
        } catch (Exception e) {
            log.warn("Agent 记忆请求异常: {}", e.getMessage());
            return Result.error("AGENT_MEMORY_UNEXPECTED_ERROR：" + e.getMessage());
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
