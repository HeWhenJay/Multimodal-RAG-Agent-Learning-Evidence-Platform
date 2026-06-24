package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentOperation {

    private String id;
    private String taskId;
    private String reviewId;
    private String userId;
    private String operationType;
    private String resourceType;
    private String resourceId;
    private String status;
    private String beforeSnapshotRef;
    private String afterSnapshotRef;
    private String idempotencyKey;
    private OffsetDateTime undoDeadline;
    private Long auditEventId;
    private String errorCode;
    private String errorMessage;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
