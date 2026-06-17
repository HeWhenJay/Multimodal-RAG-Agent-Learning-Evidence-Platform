package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
@Builder
public class JdAnalysisVO {

    private Long id;
    private String userId;
    private String jobDescription;
    private Integer matchScore;
    private Integer masteredPercent;
    private Integer partialPercent;
    private Integer gapPercent;
    private List<JdAnalysisSkillVO> skills;
    private List<JdLearningPlanItemVO> learningPlan;
    private LocalDateTime updatedAt;
}
