package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

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
    private LocalDateTime nextAttemptAt;
    private LocalDateTime leaseUntil;
    private String lockedBy;
    private LocalDateTime publishedAt;
    private String errorMessage;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
