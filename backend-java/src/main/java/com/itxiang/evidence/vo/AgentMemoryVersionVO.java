package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;

@Data
@Builder
public class AgentMemoryVersionVO {

    private String id;
    private String memoryId;
    private String previousMemoryId;
    private String relationType;
    private String decision;
    private String reason;
    private String decidedBy;
    private OffsetDateTime createdAt;
}
