package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.dto.JdAnalysisRequestDTO;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.service.PageDataService;
import com.itxiang.evidence.vo.DashboardVO;
import com.itxiang.evidence.vo.JdAnalysisVO;
import com.itxiang.evidence.vo.ResumeEvidenceAlignmentVO;
import com.itxiang.evidence.vo.SystemSettingVO;
import com.itxiang.evidence.vo.VideoSliceVO;
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
import org.springframework.web.bind.annotation.RestController;

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
    public Result<DashboardVO> dashboard(@RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("获取工作台页面数据");
        return Result.success(pageDataService.dashboard(currentUserId(authorization)));
    }

    /**
     * 获取最近一次 JD 分析页面数据。
     */
    @GetMapping("/jd-analysis")
    @Operation(summary = "获取 JD 分析页面数据")
    public Result<JdAnalysisVO> jdAnalysis(@RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("获取 JD 分析页面数据");
        return Result.success(pageDataService.latestJdAnalysis(currentUserId(authorization)));
    }

    /**
     * 提交 JD 和简历文本，运行 RAG 证据适配分析。
     */
    @PostMapping("/jd-analysis/analyze")
    @Operation(summary = "运行 JD 适配分析")
    public Result<JdAnalysisVO> analyzeJd(@Valid @RequestBody JdAnalysisRequestDTO dto,
                                          @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("运行 JD 适配分析: jdLength={}", dto.getJobDescription() == null ? 0 : dto.getJobDescription().length());
        return Result.success(pageDataService.analyzeJd(dto, currentUserId(authorization)));
    }

    /**
     * 获取简历适配页面数据。
     */
    @GetMapping("/resume-adaptation")
    @Operation(summary = "获取简历适配页面数据")
    public Result<List<ResumeEvidenceAlignmentVO>> resumeAdaptation(@RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("获取简历适配页面数据");
        return Result.success(pageDataService.resumeAlignments(currentUserId(authorization)));
    }

    /**
     * 获取视频复习页面数据。
     */
    @GetMapping("/video-review")
    @Operation(summary = "获取视频复习页面数据")
    public Result<List<VideoSliceVO>> videoReview() {
        log.info("获取视频复习页面数据");
        return Result.success(pageDataService.videoSlices());
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
