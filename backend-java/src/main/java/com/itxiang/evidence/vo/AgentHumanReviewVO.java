package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;
import java.util.Map;

@Data
@Builder
public class AgentHumanReviewVO {

    private String id;
    private String taskId;
    private String reviewType;
    private String status;
    private Map<String, Object> proposal;
    private Map<String, Object> decision;
    private String reviewedBy;
    private OffsetDateTime reviewedAt;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
    private OffsetDateTime expiresAt;
}
