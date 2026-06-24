package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentMemoryAudit {

    private String id;
    private String memoryId;
    private String userId;
    private String taskId;
    private String action;
    private String actorType;
    private String beforeHash;
    private String afterHash;
    private String summary;
    private OffsetDateTime createdAt;
}
