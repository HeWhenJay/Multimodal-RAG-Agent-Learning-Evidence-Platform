package com.itxiang.evidence.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
public class ResumeTemplateExportDTO {

    @NotNull(message = "模板版本不能为空")
    @Min(value = 1, message = "模板版本不合法")
    private Integer version;

    @NotBlank(message = "补丁草稿 ID 不能为空")
    private String patchDraftId;

    @NotBlank(message = "幂等键不能为空")
    private String idempotencyKey;
}
