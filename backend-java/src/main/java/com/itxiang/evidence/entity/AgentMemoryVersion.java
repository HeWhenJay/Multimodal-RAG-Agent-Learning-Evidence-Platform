package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentMemoryVersion {

    private String id;
    private String memoryId;
    private String previousMemoryId;
    private String relationType;
    private String decision;
    private String reason;
    private String decidedBy;
    private String userId;
    private OffsetDateTime createdAt;
}
