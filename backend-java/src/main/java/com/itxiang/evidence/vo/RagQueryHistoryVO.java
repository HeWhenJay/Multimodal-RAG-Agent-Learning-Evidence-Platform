package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Data
@Builder
public class RagQueryHistoryVO {

    private Long id;
    private String taskId;
    private String question;
    private String answer;
    private String answerStatus;
    private String refusalReason;
    private String refusalPolicy;
    private Double confidence;
    private List<String> supportingEvidenceIds;
    private String refusalMessage;
    private String status;
    private Integer topK;
    private Integer evidenceCount;
    private List<String> expandedQueries;
    private List<RagEvidenceVO> evidences;
    private Map<String, Object> diagnostics;
    private List<RagProgressVO> progressEvents;
    private String errorMessage;
    private Integer durationMs;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
