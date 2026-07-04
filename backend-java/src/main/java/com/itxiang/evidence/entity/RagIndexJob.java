package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class RagIndexJob {

    private String id;
    private Long materialId;
    private String canonicalDocumentId;
    private String stagingDocumentId;
    private String userId;
    private String operation;
    private String status;
    private Integer requestVersion;
    private String idempotencyKey;
    private Integer attempt;
    private String requestJson;
    private String resultJson;
    private String errorCode;
    private String errorMessage;
    private LocalDateTime requestedAt;
    private LocalDateTime startedAt;
    private LocalDateTime indexedAt;
    private LocalDateTime promotedAt;
    private LocalDateTime finishedAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
