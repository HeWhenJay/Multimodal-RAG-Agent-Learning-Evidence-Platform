package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentHumanReview {

    private String id;
    private String taskId;
    private String reviewType;
    private String status;
    private String proposalJson;
    private String decisionJson;
    private String reviewedBy;
    private OffsetDateTime reviewedAt;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
    private OffsetDateTime expiresAt;
}
