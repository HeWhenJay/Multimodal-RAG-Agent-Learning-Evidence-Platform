package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class LogErrorVO {

    private Long id;
    private String traceId;
    private String source;
    private String domain;
    private String severity;
    private String module;
    private String stage;
    private String action;
    private String errorType;
    private String errorCode;
    private String message;
    private String fingerprint;
    private Integer statusCode;
    private Integer durationMs;
    private Long materialId;
    private String documentId;
    private String parser;
    private String contextJson;
    private Integer occurrenceCount;
    private String status;
    private LocalDateTime firstSeenAt;
    private LocalDateTime lastSeenAt;
    private LocalDateTime createdAt;
}
