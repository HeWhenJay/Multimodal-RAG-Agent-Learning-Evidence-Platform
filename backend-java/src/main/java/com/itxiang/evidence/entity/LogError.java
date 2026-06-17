package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class LogError {

    private Long id;
    private String traceId;
    private String sessionId;
    private String userId;
    private String source;
    private String domain;
    private String severity;
    private String module;
    private String stage;
    private String action;
    private String errorType;
    private String errorCode;
    private String message;
    private String stackTrace;
    private String fingerprint;
    private String route;
    private String httpMethod;
    private String requestPath;
    private Integer statusCode;
    private Integer durationMs;
    private Long materialId;
    private String documentId;
    private String parser;
    private OffsetDateTime clientTime;
    private OffsetDateTime serverTime;
    private String contextJson;
    private OffsetDateTime firstSeenAt;
    private OffsetDateTime lastSeenAt;
    private Integer occurrenceCount;
    private String status;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
