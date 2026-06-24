package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.LinkedHashMap;
import java.util.Map;

@Data
public class AgentReviewDecisionDTO {

    @NotBlank(message = "审批决策不能为空")
    private String decision;

    private String comment;

    private Map<String, Object> changes = new LinkedHashMap<>();
}
