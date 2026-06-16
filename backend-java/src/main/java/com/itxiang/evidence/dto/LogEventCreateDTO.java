package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.Map;

@Data
public class LogEventCreateDTO {

    private String traceId;
    private String sessionId;
    private String userId;
    private String source = "java";
    private String domain = "system";
    private String level = "INFO";

    @NotBlank(message = "module cannot be blank")
    private String module;

    private String stage;
    private String eventType = "business_state";

    @NotBlank(message = "action cannot be blank")
    private String action;

    private String message;
    private String route;
    private String httpMethod;
    private String requestPath;
    private Integer statusCode;
    private Boolean success = true;
    private Integer durationMs;
    private Long materialId;
    private String documentId;
    private String parser;
    private LocalDateTime clientTime;
    private Map<String, Object> context = new HashMap<>();
}
