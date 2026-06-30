package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

import java.time.OffsetDateTime;
import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AgentChatMessageVO {

    private String id;
    private String taskId;
    private Long sequenceNo;
    private String role;
    private String messageType;
    private String content;
    private Map<String, Object> payload;
    private String sourceEventType;
    private String sourceId;
    private String dedupeKey;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
