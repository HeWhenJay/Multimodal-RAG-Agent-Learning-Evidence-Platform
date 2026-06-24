package com.itxiang.evidence.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
public class ResumePatchValidateDTO {

    @NotNull(message = "模板版本不能为空")
    @Min(value = 1, message = "模板版本不合法")
    private Integer version;

    @NotBlank(message = "补丁草稿 ID 不能为空")
    private String patchDraftId;

    @NotEmpty(message = "补丁列表不能为空")
    private List<Map<String, Object>> patches;
}
