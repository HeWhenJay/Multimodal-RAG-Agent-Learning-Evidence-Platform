package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class RagIndexTextDTO {

    @NotBlank(message = "标题不能为空")
    private String title;

    private String documentType = "markdown";

    private String source = "manual";

    private String visibilityScope = "private";

    @NotBlank(message = "内容不能为空")
    private String content;
}

