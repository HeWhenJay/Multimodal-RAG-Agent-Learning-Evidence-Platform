package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.JdAnalysisRequestDTO;
import com.itxiang.evidence.vo.DashboardVO;
import com.itxiang.evidence.vo.JdAnalysisVO;
import com.itxiang.evidence.vo.ResumeEvidenceAlignmentVO;
import com.itxiang.evidence.vo.SystemSettingVO;
import com.itxiang.evidence.vo.VideoSliceVO;

import java.time.LocalDate;
import java.util.List;

public interface PageDataService {

    /**
     * 获取工作台页面所需的数据库聚合数据。
     */
    DashboardVO dashboard(String userId, LocalDate startDate, LocalDate endDate, Integer recentDays, Integer recentLimit);

    /**
     * 获取最近一次 JD 分析数据。
     */
    JdAnalysisVO latestJdAnalysis(String userId);

    /**
     * 运行 JD 与简历证据适配分析并保存结果。
     */
    JdAnalysisVO analyzeJd(JdAnalysisRequestDTO dto, String userId);

    /**
     * 获取简历证据对齐数据。
     */
    List<ResumeEvidenceAlignmentVO> resumeAlignments(String userId);

    /**
     * 获取视频复习切片数据。
     */
    List<VideoSliceVO> videoSlices();

    /**
     * 获取系统设置展示数据。
     */
    List<SystemSettingVO> systemSettings();
}
