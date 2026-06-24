package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentTask {

    private String id;
    private String userId;
    private String taskType;
    private String status;
    private String title;
    private String inputJson;
    private String planJson;
    private String draftJson;
    private String finalJson;
    private String pythonThreadId;
    private String errorCode;
    private String errorMessage;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
