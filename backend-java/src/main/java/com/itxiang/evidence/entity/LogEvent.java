package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class LogEvent {

    private Long id;
    private String traceId;
    private String sessionId;
    private String userId;
    private String source;
    private String domain;
    private String level;
    private String module;
    private String stage;
    private String eventType;
    private String action;
    private String message;
    private String route;
    private String httpMethod;
    private String requestPath;
    private Integer statusCode;
    private Boolean success;
    private Integer durationMs;
    private Long materialId;
    private String documentId;
    private String parser;
    private OffsetDateTime clientTime;
    private OffsetDateTime serverTime;
    private String contextJson;
    private OffsetDateTime createdAt;
}
