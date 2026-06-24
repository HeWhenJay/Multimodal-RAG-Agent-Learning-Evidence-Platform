package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
@Builder
public class RagQueryVO {

    private String answer;
    private String answerStatus;
    private String refusalReason;
    private String refusalPolicy;
    private Double confidence;
    private List<String> supportingEvidenceIds;
    private String refusalMessage;
    private List<String> expandedQueries;
    private List<RagEvidenceVO> evidences;
    private Map<String, Object> diagnostics;
    private List<RagProgressVO> progressEvents;
}
