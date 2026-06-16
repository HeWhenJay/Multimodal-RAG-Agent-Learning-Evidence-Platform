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
@Tag(name = "Logs", description = "System observation log APIs")
public class LogController {

    private final LogService logService;
    private final LogProperties logProperties;

    @PostMapping("/events")
    @Operation(summary = "Record a business event log")
    public Result<Long> recordEvent(@Valid @RequestBody LogEventCreateDTO dto) {
        return Result.success(logService.recordEvent(dto));
    }

    @PostMapping("/events/batch")
    @Operation(summary = "Record business event logs in batch")
    public Result<Integer> recordEvents(@Valid @RequestBody List<LogEventCreateDTO> dtoList) {
        return Result.success(logService.recordEvents(dtoList));
    }

    @PostMapping("/errors")
    @Operation(summary = "Record an error log")
    public Result<Long> recordError(@Valid @RequestBody LogErrorCreateDTO dto) {
        return Result.success(logService.recordError(dto));
    }

    @PostMapping("/internal/errors")
    @Operation(summary = "Record an internal service error log")
    public Result<Long> recordInternalError(@RequestHeader(value = "X-Internal-Log-Token", required = false) String token,
                                            @Valid @RequestBody LogErrorCreateDTO dto) {
        if (!internalTokenValid(token)) {
            return Result.error("invalid internal log token");
        }
        if (dto.getSource() == null || dto.getSource().isBlank() || "java".equals(dto.getSource())) {
            dto.setSource("python");
        }
        return Result.success(logService.recordError(dto));
    }

    @GetMapping("/events/recent")
    @Operation(summary = "List recent business event logs")
    public Result<List<LogEventVO>> recentEvents(@RequestParam(defaultValue = "50") Integer limit) {
        return Result.success(logService.listRecentEvents(limit));
    }

    @GetMapping("/errors/recent")
    @Operation(summary = "List recent error logs")
    public Result<List<LogErrorVO>> recentErrors(@RequestParam(defaultValue = "50") Integer limit) {
        return Result.success(logService.listRecentErrors(limit));
    }

    @GetMapping("/overview")
    @Operation(summary = "Get log overview")
    public Result<LogOverviewVO> overview(@RequestParam(defaultValue = "7") Integer days) {
        return Result.success(logService.overview(days));
    }

    private boolean internalTokenValid(String token) {
        String configured = logProperties.getInternalToken();
        return configured == null || configured.isBlank() || configured.equals(token);
    }
}
