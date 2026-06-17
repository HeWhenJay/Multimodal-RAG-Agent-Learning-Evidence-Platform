package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class JdAnalysisRequestDTO {

    @NotBlank(message = "岗位描述不能为空")
    private String jobDescription;

    private String resumeText;
}
