package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;

@Data
@Builder
public class AgentOperationVO {

    private String id;
    private String taskId;
    private String reviewId;
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
