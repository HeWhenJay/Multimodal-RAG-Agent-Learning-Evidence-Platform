package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

@Data
@Builder
public class AgentMemoryVO {

    private String id;
    private String userId;
    private String memoryType;
    private String namespace;
    private String scopeType;
    private String scopeId;
    private String subjectKey;
    private String content;
    private String summary;
    private List<Map<String, Object>> evidenceRefs;
    private String sourceTaskId;
    private String sourceToolCallId;
    private String sourceReviewId;
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
