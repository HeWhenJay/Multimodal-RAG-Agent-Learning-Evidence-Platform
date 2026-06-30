package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.service.PageDataService;
import com.itxiang.evidence.vo.DashboardVO;
import com.itxiang.evidence.vo.SystemSettingVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.List;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/page-data")
@Tag(name = "页面数据", description = "后台页面数据库数据接口")
public class PageDataController {

    private final PageDataService pageDataService;
    private final AuthService authService;

    /**
     * 获取工作台页面聚合数据。
     */
    @GetMapping("/dashboard")
    @Operation(summary = "获取工作台页面数据")
    public Result<DashboardVO> dashboard(@RequestHeader(value = "Authorization", required = false) String authorization,
                                         @RequestParam(value = "startDate", required = false)
                                         @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate startDate,
                                         @RequestParam(value = "endDate", required = false)
                                         @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate endDate,
                                         @RequestParam(value = "recentDays", required = false) Integer recentDays,
                                         @RequestParam(value = "recentLimit", defaultValue = "5") Integer recentLimit) {
        log.info("获取工作台页面数据: startDate={}, endDate={}, recentDays={}, recentLimit={}",
                startDate, endDate, recentDays, recentLimit);
        return Result.success(pageDataService.dashboard(currentUserId(authorization), startDate, endDate, recentDays, recentLimit));
    }

    /**
     * 获取系统设置页面展示数据。
     */
    @GetMapping("/settings")
    @Operation(summary = "获取系统设置页面数据")
    public Result<List<SystemSettingVO>> settings() {
        log.info("获取系统设置页面数据");
        return Result.success(pageDataService.systemSettings());
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
