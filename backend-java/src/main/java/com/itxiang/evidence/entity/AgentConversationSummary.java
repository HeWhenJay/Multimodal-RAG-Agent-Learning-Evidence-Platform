package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentConversationSummary {

    private String id;
    private String taskId;
    private String userId;
    private String summaryType;
    private String coveredMessageStartId;
    private String coveredMessageEndId;
    private Integer coveredMessageCount;
    private Integer rawTokenEstimate;
    private Integer compressedTokenEstimate;
    private String summaryJson;
    private String summaryText;
    private String keyFactsJson;
    private String evidenceRefsJson;
    private String compressionModel;
    private String compressionPromptVersion;
    private Integer compressionVersion;
    private String status;
    private String diagnosticsJson;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
