package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class AgentToolDefinitionVO {

    private String toolName;
    private String toolType;
    private Boolean requiresReview;
    private String approvalType;
    private Integer stage;
    private String description;
}
