package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentToolCall {

    private String id;
    private String taskId;
    private String toolName;
    private String toolType;
    private String status;
    private String requestJson;
    private String responseJson;
    private Boolean ownershipVerified;
    private String scope;
    private String errorCode;
    private String errorMessage;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
