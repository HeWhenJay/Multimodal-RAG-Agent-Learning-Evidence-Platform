package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class RagConsumedEvent {

    private Long id;
    private String consumerName;
    private String messageId;
    private String messageType;
    private String idempotencyKey;
    private String jobId;
    private Integer progressSequence;
    private String status;
    private String errorMessage;
    private LocalDateTime consumedAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
