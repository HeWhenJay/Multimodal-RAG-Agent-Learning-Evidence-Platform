package com.itxiang.evidence.dto;

import lombok.Data;

@Data
public class AgentMemoryPatchDTO {

    private String content;
    private String summary;
    private String namespace;
    private String subjectKey;
    private String scopeType;
    private String scopeId;
}
