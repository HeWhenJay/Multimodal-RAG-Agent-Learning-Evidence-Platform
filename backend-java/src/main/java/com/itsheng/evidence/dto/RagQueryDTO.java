package com.itsheng.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.HashMap;
import java.util.Map;

@Data
public class RagQueryDTO {

    @NotBlank(message = "问题不能为空")
    private String question;

    private Integer topK = 5;

    private Map<String, Object> metadataFilter = new HashMap<>();
}

