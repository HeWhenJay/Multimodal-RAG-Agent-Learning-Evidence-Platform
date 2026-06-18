package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.config.LogProperties;
import com.itxiang.evidence.dto.LogErrorCreateDTO;
import com.itxiang.evidence.dto.LogEventCreateDTO;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.vo.LogErrorVO;
import com.itxiang.evidence.vo.LogEventVO;
import com.itxiang.evidence.vo.LogOverviewVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/logs")
@Tag(name = "系统日志", description = "系统观测日志接口")
public class LogController {

    private final LogService logService;
    private final LogProperties logProperties;

    /**
     * 接收单条业务事件日志。
     */
    @PostMapping("/events")
    @Operation(summary = "记录业务事件日志")
    public Result<Long> recordEvent(@Valid @RequestBody LogEventCreateDTO dto) {
        return Result.success(logService.recordEvent(dto));
    }

    /**
     * 批量接收业务事件日志。
     */
    @PostMapping("/events/batch")
    @Operation(summary = "批量记录业务事件日志")
    public Result<Integer> recordEvents(@Valid @RequestBody List<LogEventCreateDTO> dtoList) {
        return Result.success(logService.recordEvents(dtoList));
    }

    /**
     * 接收 Python 等内部服务上报的业务事件和 RAG 进度日志。
     */
    @PostMapping("/internal/events")
    @Operation(summary = "记录内部服务业务事件日志")
    public Result<Long> recordInternalEvent(@RequestHeader(value = "X-Internal-Log-Token", required = false) String token,
                                            @Valid @RequestBody LogEventCreateDTO dto) {
        if (!internalTokenValid(token)) {
            return Result.error("内部日志令牌无效");
        }
        if (dto.getSource() == null || dto.getSource().isBlank() || "java".equals(dto.getSource())) {
            dto.setSource("python");
        }
        return Result.success(logService.recordEvent(dto));
    }

    /**
     * 接收单条错误日志。
     */
    @PostMapping("/errors")
    @Operation(summary = "记录错误日志")
    public Result<Long> recordError(@Valid @RequestBody LogErrorCreateDTO dto) {
        return Result.success(logService.recordError(dto));
    }

    /**
     * 接收 Python 等内部服务上报的错误日志。
     */
    @PostMapping("/internal/errors")
    @Operation(summary = "记录内部服务错误日志")
    public Result<Long> recordInternalError(@RequestHeader(value = "X-Internal-Log-Token", required = false) String token,
                                            @Valid @RequestBody LogErrorCreateDTO dto) {
        if (!internalTokenValid(token)) {
            return Result.error("内部日志令牌无效");
        }
        if (dto.getSource() == null || dto.getSource().isBlank() || "java".equals(dto.getSource())) {
            dto.setSource("python");
        }
        return Result.success(logService.recordError(dto));
    }

    /**
     * 查询最近业务事件日志。
     */
    @GetMapping("/events/recent")
    @Operation(summary = "查询最近业务事件日志")
    public Result<List<LogEventVO>> recentEvents(@RequestParam(defaultValue = "50") Integer limit) {
        return Result.success(logService.listRecentEvents(limit));
    }

    /**
     * 查询最近错误日志。
     */
    @GetMapping("/errors/recent")
    @Operation(summary = "查询最近错误日志")
    public Result<List<LogErrorVO>> recentErrors(@RequestParam(defaultValue = "50") Integer limit) {
        return Result.success(logService.listRecentErrors(limit));
    }

    /**
     * 查询日志概览统计。
     */
    @GetMapping("/overview")
    @Operation(summary = "查询日志概览")
    public Result<LogOverviewVO> overview(@RequestParam(defaultValue = "7") Integer days) {
        return Result.success(logService.overview(days));
    }

    /**
     * 校验内部日志上报令牌；本地未配置令牌时默认放行。
     */
    private boolean internalTokenValid(String token) {
        String configured = logProperties.getInternalToken();
        return configured == null || configured.isBlank() || configured.equals(token);
    }
}
