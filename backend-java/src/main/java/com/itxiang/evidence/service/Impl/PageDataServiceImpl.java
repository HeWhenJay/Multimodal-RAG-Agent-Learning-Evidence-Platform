package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.JdAnalysisRequestDTO;
import com.itxiang.evidence.entity.JdAnalysisReport;
import com.itxiang.evidence.entity.JdAnalysisSkill;
import com.itxiang.evidence.entity.JdLearningPlanItem;
import com.itxiang.evidence.entity.ResumeEvidenceAlignment;
import com.itxiang.evidence.entity.SystemSetting;
import com.itxiang.evidence.entity.VideoSlice;
import com.itxiang.evidence.mapper.JdAnalysisMapper;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.ResumeEvidenceAlignmentMapper;
import com.itxiang.evidence.mapper.SystemSettingMapper;
import com.itxiang.evidence.mapper.VideoSliceMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.PageDataService;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.DashboardVO;
import com.itxiang.evidence.vo.JdAnalysisSkillVO;
import com.itxiang.evidence.vo.JdAnalysisVO;
import com.itxiang.evidence.vo.JdLearningPlanItemVO;
import com.itxiang.evidence.vo.LogOverviewVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.ResumeEvidenceAlignmentVO;
import com.itxiang.evidence.vo.SystemSettingVO;
import com.itxiang.evidence.vo.VideoSliceVO;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class PageDataServiceImpl implements PageDataService {

    private static final int DASHBOARD_RECENT_LIMIT = 3;
    private static final int PAGE_LIST_LIMIT = 50;

    private final RagService ragService;
    private final PythonRagClient pythonRagClient;
    private final LogService logService;
    private final LearningMaterialMapper learningMaterialMapper;
    private final VideoSliceMapper videoSliceMapper;
    private final ResumeEvidenceAlignmentMapper resumeEvidenceAlignmentMapper;
    private final JdAnalysisMapper jdAnalysisMapper;
    private final SystemSettingMapper systemSettingMapper;

    /**
     * 聚合工作台统计、最近资料、视频切片、JD 分析和简历证据数据。
     */
    @Override
    public DashboardVO dashboard(String userId) {
        LocalDateTime sevenDaysAgo = LocalDateTime.now().minusDays(7);
        RagOverviewVO ragOverview = ragService.overview(userId);
        LogOverviewVO logOverview = logService.overview(30);
        return DashboardVO.builder()
                .materialCount(defaultLong(ragOverview.getMaterialCount()))
                .materialDelta7Days(defaultLong(learningMaterialMapper.countSinceByUserId(userId, sevenDaysAgo)))
                .videoSliceCount(defaultLong(videoSliceMapper.countAll()))
                .videoSliceDelta7Days(defaultLong(videoSliceMapper.countSince(sevenDaysAgo)))
                .evidenceCount(defaultInt(ragOverview.getChunkCount()))
                .openErrorCount(defaultLong(logOverview.getOpenErrorCount()))
                .errorCount30Days(defaultLong(logOverview.getErrorCount()))
                .recentMaterials(ragService.listRecentMaterials(userId).stream().limit(DASHBOARD_RECENT_LIMIT).toList())
                .recentVideoSlices(videoSliceMapper.findRecent(DASHBOARD_RECENT_LIMIT).stream().map(this::toVideoSliceVO).toList())
                .latestJdAnalysis(latestJdAnalysis(userId))
                .resumeAlignments(resumeEvidenceAlignmentMapper.findRecentByUserId(userId, DASHBOARD_RECENT_LIMIT).stream()
                        .map(this::toResumeEvidenceAlignmentVO)
                        .toList())
                .build();
    }

    /**
     * 查询最近一次 JD 分析并组装技能项和学习计划。
     */
    @Override
    public JdAnalysisVO latestJdAnalysis(String userId) {
        JdAnalysisReport report = jdAnalysisMapper.findLatestReportByUserId(userId);
        if (report == null) {
            return null;
        }
        List<JdAnalysisSkillVO> skills = jdAnalysisMapper.findSkillsByReportId(report.getId()).stream()
                .map(this::toJdAnalysisSkillVO)
                .toList();
        List<JdLearningPlanItemVO> learningPlan = jdAnalysisMapper.findPlanByReportId(report.getId()).stream()
                .map(this::toJdLearningPlanItemVO)
                .toList();
        return JdAnalysisVO.builder()
                .id(report.getId())
                .userId(report.getUserId())
                .jobDescription(report.getJobDescription())
                .matchScore(defaultInt(report.getMatchScore()))
                .masteredPercent(defaultInt(report.getMasteredPercent()))
                .partialPercent(defaultInt(report.getPartialPercent()))
                .gapPercent(defaultInt(report.getGapPercent()))
                .skills(skills)
                .learningPlan(learningPlan)
                .updatedAt(report.getUpdatedAt())
                .build();
    }

    /**
     * 调用 Python RAG 分析 JD 与简历文本，并保存报告、技能项、学习计划和证据对齐记录。
     */
    @Override
    @Transactional
    public JdAnalysisVO analyzeJd(JdAnalysisRequestDTO dto, String userId) {
        PythonRagClient.JdAnalysisResult result = pythonRagClient.analyzeJd(userId, dto);
        JdAnalysisReport report = new JdAnalysisReport();
        report.setUserId(userId);
        report.setReportKey("jd-" + UUID.randomUUID());
        report.setJobDescription(result.jobDescription());
        report.setMatchScore(defaultInt(result.matchScore()));
        report.setMasteredPercent(defaultInt(result.masteredPercent()));
        report.setPartialPercent(defaultInt(result.partialPercent()));
        report.setGapPercent(defaultInt(result.gapPercent()));
        jdAnalysisMapper.insertReport(report);

        for (PythonRagClient.JdSkillResult item : result.skills()) {
            JdAnalysisSkill skill = new JdAnalysisSkill();
            skill.setReportId(report.getId());
            skill.setSkillName(item.skillName());
            skill.setStatus(item.status());
            jdAnalysisMapper.insertSkill(skill);
        }

        for (PythonRagClient.JdPlanResult item : result.learningPlan()) {
            JdLearningPlanItem planItem = new JdLearningPlanItem();
            planItem.setReportId(report.getId());
            planItem.setStepNo(item.stepNo());
            planItem.setTitle(item.title());
            planItem.setDescription(item.description());
            jdAnalysisMapper.insertPlanItem(planItem);
        }

        resumeEvidenceAlignmentMapper.deleteByUserId(userId);
        for (PythonRagClient.ResumeAlignmentResult item : result.resumeAlignments()) {
            ResumeEvidenceAlignment alignment = new ResumeEvidenceAlignment();
            alignment.setUserId(userId);
            alignment.setRequirement(item.requirement());
            alignment.setEvidence(item.evidence());
            alignment.setStatus(item.status());
            resumeEvidenceAlignmentMapper.insert(alignment);
        }

        return latestJdAnalysis(userId);
    }

    /**
     * 查询简历证据对齐记录列表。
     */
    @Override
    public List<ResumeEvidenceAlignmentVO> resumeAlignments(String userId) {
        return resumeEvidenceAlignmentMapper.findRecentByUserId(userId, PAGE_LIST_LIMIT).stream()
                .map(this::toResumeEvidenceAlignmentVO)
                .toList();
    }

    /**
     * 查询视频复习切片列表。
     */
    @Override
    public List<VideoSliceVO> videoSlices() {
        return videoSliceMapper.findRecent(PAGE_LIST_LIMIT).stream()
                .map(this::toVideoSliceVO)
                .toList();
    }

    /**
     * 查询系统设置展示项。
     */
    @Override
    public List<SystemSettingVO> systemSettings() {
        return systemSettingMapper.findAll().stream()
                .map(this::toSystemSettingVO)
                .toList();
    }

    /**
     * 转换视频切片展示对象。
     */
    private VideoSliceVO toVideoSliceVO(VideoSlice slice) {
        return VideoSliceVO.builder()
                .id(slice.getId())
                .title(slice.getTitle())
                .topic(slice.getTopic())
                .startTime(slice.getStartTime())
                .endTime(slice.getEndTime())
                .status(slice.getStatus())
                .createdAt(slice.getCreatedAt())
                .updatedAt(slice.getUpdatedAt())
                .build();
    }

    /**
     * 转换简历证据对齐展示对象。
     */
    private ResumeEvidenceAlignmentVO toResumeEvidenceAlignmentVO(ResumeEvidenceAlignment alignment) {
        return ResumeEvidenceAlignmentVO.builder()
                .id(alignment.getId())
                .userId(alignment.getUserId())
                .requirement(alignment.getRequirement())
                .evidence(alignment.getEvidence())
                .status(alignment.getStatus())
                .createdAt(alignment.getCreatedAt())
                .updatedAt(alignment.getUpdatedAt())
                .build();
    }

    /**
     * 转换 JD 技能匹配展示对象。
     */
    private JdAnalysisSkillVO toJdAnalysisSkillVO(JdAnalysisSkill skill) {
        return JdAnalysisSkillVO.builder()
                .id(skill.getId())
                .skillName(skill.getSkillName())
                .status(skill.getStatus())
                .build();
    }

    /**
     * 转换 JD 学习计划展示对象。
     */
    private JdLearningPlanItemVO toJdLearningPlanItemVO(JdLearningPlanItem item) {
        return JdLearningPlanItemVO.builder()
                .id(item.getId())
                .stepNo(item.getStepNo())
                .title(item.getTitle())
                .description(item.getDescription())
                .build();
    }

    /**
     * 转换系统设置展示对象。
     */
    private SystemSettingVO toSystemSettingVO(SystemSetting setting) {
        return SystemSettingVO.builder()
                .key(setting.getSettingKey())
                .group(setting.getSettingGroup())
                .label(setting.getLabel())
                .value(setting.getSettingValue())
                .sortOrder(setting.getSortOrder())
                .build();
    }

    /**
     * 为 Long 空值提供 0 默认值。
     */
    private Long defaultLong(Long value) {
        return value == null ? 0L : value;
    }

    /**
     * 为 Integer 空值提供 0 默认值。
     */
    private Integer defaultInt(Integer value) {
        return value == null ? 0 : value;
    }
}
