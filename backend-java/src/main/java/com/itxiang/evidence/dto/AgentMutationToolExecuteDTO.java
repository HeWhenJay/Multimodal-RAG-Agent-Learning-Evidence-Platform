package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.LinkedHashMap;
import java.util.Map;

@Data
public class AgentMutationToolExecuteDTO {

    @NotBlank(message = "任务 ID 不能为空")
    private String taskId;

    private String toolCallId;

    @NotBlank(message = "审批 ID 不能为空")
    private String approvalId;

    private String operationId;

    @NotBlank(message = "工具名称不能为空")
    private String toolName;

    @NotBlank(message = "幂等键不能为空")
    private String idempotencyKey;

    private Map<String, Object> arguments = new LinkedHashMap<>();
}
