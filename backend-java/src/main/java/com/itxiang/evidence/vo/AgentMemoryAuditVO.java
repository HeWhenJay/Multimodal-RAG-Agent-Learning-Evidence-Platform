package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;

@Data
@Builder
public class AgentMemoryAuditVO {

    private String id;
    private String memoryId;
    private String taskId;
    private String action;
    private String actorType;
    private String summary;
    private OffsetDateTime createdAt;
}
