package com.itxiang.evidence.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
public class ResumePatchGenerateDTO {

    @NotNull(message = "模板版本不能为空")
    @Min(value = 1, message = "模板版本不合法")
    private Integer version;

    @NotBlank(message = "岗位 JD 不能为空")
    private String jobDescription;

    private String resumeText;

    private Long resumeMaterialId;

    private String resumeMaterialTitle;

    private Integer topK = 5;

    private Boolean useConfirmedAnnotations = false;
}
