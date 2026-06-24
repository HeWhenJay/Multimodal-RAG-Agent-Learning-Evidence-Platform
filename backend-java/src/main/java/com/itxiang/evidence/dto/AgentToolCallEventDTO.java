package com.itxiang.evidence.dto;

import lombok.Data;

import java.util.LinkedHashMap;
import java.util.Map;

@Data
public class AgentToolCallEventDTO {

    private String id;
    private String toolName;
    private String toolType = "READ";
    private String status;
    private Map<String, Object> response = new LinkedHashMap<>();
    private Boolean ownershipVerified;
    private String scope;
    private String errorCode;
    private String errorMessage;
}
