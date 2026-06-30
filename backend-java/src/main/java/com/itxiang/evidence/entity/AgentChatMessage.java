package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.OffsetDateTime;

@Data
public class AgentChatMessage {

    private String id;
    private String taskId;
    private String userId;
    private Long sequenceNo;
    private String role;
    private String messageType;
    private String content;
    private String payloadJson;
    private String sourceEventType;
    private String sourceId;
    private String dedupeKey;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
