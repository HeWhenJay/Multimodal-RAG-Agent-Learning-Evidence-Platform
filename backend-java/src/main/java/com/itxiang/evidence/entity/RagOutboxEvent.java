package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class RagOutboxEvent {

    private Long id;
    private String topic;
    private String messageKey;
    private String eventType;
    private String idempotencyKey;
    private String payloadJson;
    private String status;
    private Integer attempt;
    private OffsetDateTime nextAttemptAt;
    private OffsetDateTime leaseUntil;
    private String lockedBy;
    private OffsetDateTime publishedAt;
    private String errorMessage;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
