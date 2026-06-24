package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;
import java.util.Map;

@Data
@Builder
public class AgentToolCallVO {

    private String id;
    private String taskId;
    private String toolName;
    private String toolType;
    private String status;
    private Map<String, Object> request;
    private Map<String, Object> response;
    private Boolean ownershipVerified;
    private String scope;
    private String errorCode;
    private String errorMessage;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
