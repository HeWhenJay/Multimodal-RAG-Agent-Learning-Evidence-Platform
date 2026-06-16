package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.Map;

@Data
public class LogErrorCreateDTO {

    private String traceId;
    private String sessionId;
    private String userId;
    private String source = "java";
    private String domain = "system";
    private String severity = "ERROR";

    @NotBlank(message = "module cannot be blank")
    private String module;

    private String stage;
    private String action;

    @NotBlank(message = "errorType cannot be blank")
    private String errorType;

    private String errorCode;

    @NotBlank(message = "message cannot be blank")
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
    private LocalDateTime clientTime;
    private Map<String, Object> context = new HashMap<>();
}
