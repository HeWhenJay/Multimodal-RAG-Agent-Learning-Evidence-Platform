package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class AgentOperationUndoDTO {

    @NotBlank(message = "幂等键不能为空")
    private String idempotencyKey;

    private String reason;
}
