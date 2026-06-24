package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.LinkedHashMap;
import java.util.Map;

@Data
public class AgentReadToolRequestDTO {

    @NotBlank(message = "任务 ID 不能为空")
    private String taskId;

    private String toolCallId;

    @NotBlank(message = "工具名不能为空")
    private String toolName;

    private Map<String, Object> arguments = new LinkedHashMap<>();
}
