package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.LinkedHashMap;
import java.util.Map;

@Data
public class AgentTaskCreateDTO {

    @NotBlank(message = "任务类型不能为空")
    private String taskType;

    private String title;

    private Map<String, Object> input = new LinkedHashMap<>();
}
