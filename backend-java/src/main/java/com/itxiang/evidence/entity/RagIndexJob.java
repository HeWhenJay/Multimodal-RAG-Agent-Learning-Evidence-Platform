package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

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
    private OffsetDateTime requestedAt;
    private OffsetDateTime startedAt;
    private OffsetDateTime indexedAt;
    private OffsetDateTime promotedAt;
    private OffsetDateTime finishedAt;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
