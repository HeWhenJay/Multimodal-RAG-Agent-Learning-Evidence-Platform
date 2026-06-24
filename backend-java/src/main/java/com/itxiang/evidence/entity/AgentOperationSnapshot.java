package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentOperationSnapshot {

    private String id;
    private String operationId;
    private String snapshotType;
    private String resourceType;
    private String resourceId;
    private String snapshotJson;
    private String contentHash;
    private OffsetDateTime createdAt;
}
