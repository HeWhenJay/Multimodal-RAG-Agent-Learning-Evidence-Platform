package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class RagQueryHistory {

    private Long id;
    private String userId;
    private String taskId;
    private String question;
    private String answer;
    private String status;
    private Integer topK;
    private Integer evidenceCount;
    private String expandedQueriesJson;
    private String evidencesJson;
    private String diagnosticsJson;
    private String progressEventsJson;
    private String errorMessage;
    private Integer durationMs;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
