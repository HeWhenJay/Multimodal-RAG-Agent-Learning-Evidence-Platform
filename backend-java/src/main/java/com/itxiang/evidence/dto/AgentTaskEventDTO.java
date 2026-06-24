package com.itxiang.evidence.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.LinkedHashMap;
import java.util.Map;

@Data
public class AgentTaskEventDTO {

    private String eventType;
    private String status;
    private String pythonThreadId;
    private AgentToolCallEventDTO toolCall;
    private Map<String, Object> draft = new LinkedHashMap<>();
    @JsonProperty("final")
    private Map<String, Object> finalResult;
    private Map<String, Object> reviewRequest;
    private String errorCode;
    private String errorMessage;
}
