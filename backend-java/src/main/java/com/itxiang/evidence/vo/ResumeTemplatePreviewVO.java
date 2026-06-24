package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Data
@Builder
public class ResumeTemplatePreviewVO {

    private String templateId;
    private Integer version;
    private String previewStatus;
    private List<Map<String, Object>> pages;
    private List<Map<String, Object>> annotations;
    private List<Map<String, Object>> unmappedFields;
    private List<String> warnings;
    private LocalDateTime generatedAt;
}
