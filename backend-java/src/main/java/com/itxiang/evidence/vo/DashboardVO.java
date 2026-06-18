package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data
@Builder
public class DashboardVO {

    private Long materialCount;
    private Long materialDelta7Days;
    private Long videoSliceCount;
    private Long videoSliceDelta7Days;
    private Integer evidenceCount;
    private Long openErrorCount;
    private Long errorCount30Days;
    private String recentTaskStartDate;
    private String recentTaskEndDate;
    private Integer recentTaskLimit;
    private List<LearningMaterialVO> recentMaterials;
    private List<VideoSliceVO> recentVideoSlices;
    private JdAnalysisVO latestJdAnalysis;
    private List<ResumeEvidenceAlignmentVO> resumeAlignments;
}
