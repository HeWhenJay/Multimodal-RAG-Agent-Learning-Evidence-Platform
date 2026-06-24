package com.itxiang.evidence.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
public class ResumeTemplateAnnotationSaveDTO {

    @NotNull(message = "模板版本不能为空")
    @Min(value = 1, message = "模板版本不合法")
    private Integer version;

    @Valid
    @NotEmpty(message = "标注列表不能为空")
    private List<AnnotationItem> annotations;

    @Data
    public static class AnnotationItem {

        private String annotationId;
        private String fieldId;

        @NotNull(message = "页码不能为空")
        @Min(value = 0, message = "页码不合法")
        private Integer pageIndex;

        @NotNull(message = "区域坐标不能为空")
        private Map<String, Object> rect;

        private String sourceType;
        private Boolean editable;
        private String sectionKey;
        private String userInstruction;
        private String requiredEvidencePolicy;
        private String status;
    }
}
