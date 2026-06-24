package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Data
public class AgentMemoryCreateDTO {

    @NotBlank(message = "记忆类型不能为空")
    private String memoryType;

    @NotBlank(message = "记忆命名空间不能为空")
    private String namespace;

    @NotBlank(message = "记忆作用域不能为空")
    private String scopeType;

    private String scopeId;

    @NotBlank(message = "记忆主题键不能为空")
    private String subjectKey;

    @NotBlank(message = "记忆内容不能为空")
    private String content;

    private String summary;
    private List<Map<String, Object>> evidenceRefs = new ArrayList<>();
    private BigDecimal importance;
}
