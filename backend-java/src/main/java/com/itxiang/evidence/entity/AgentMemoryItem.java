package com.itxiang.evidence.entity;

import lombok.Data;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

@Data
public class AgentMemoryItem {

    private String id;
    private String userId;
    private String memoryType;
    private String namespace;
    private String scopeType;
    private String scopeId;
    private String subjectKey;
    private String content;
    private String summary;
    private String evidenceRefsJson;
    private String sourceTaskId;
    private String sourceToolCallId;
    private String sourceReviewId;
    private String sourceHash;
    private String status;
    private BigDecimal confidence;
    private BigDecimal importance;
    private String sensitivityLevel;
    private String consentSource;
    private Integer accessCount;
    private OffsetDateTime lastAccessedAt;
    private OffsetDateTime validFrom;
    private OffsetDateTime validUntil;
    private OffsetDateTime deletedAt;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
