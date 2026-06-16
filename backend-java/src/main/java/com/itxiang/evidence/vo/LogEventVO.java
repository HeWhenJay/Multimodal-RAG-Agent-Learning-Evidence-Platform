package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class LogEventVO {

    private Long id;
    private String traceId;
    private String source;
    private String domain;
    private String level;
    private String module;
    private String stage;
    private String eventType;
    private String action;
    private String message;
    private Boolean success;
    private Integer durationMs;
    private Long materialId;
    private String documentId;
    private String parser;
    private String contextJson;
    private LocalDateTime createdAt;
}
