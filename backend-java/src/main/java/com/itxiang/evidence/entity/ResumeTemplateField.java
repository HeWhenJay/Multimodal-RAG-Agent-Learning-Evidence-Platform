package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class ResumeTemplateField {

    private String id;
    private String templateId;
    private String userId;
    private Integer templateVersion;
    private String fieldId;
    private String sectionKey;
    private String displayName;
    private String sourceText;
    private String sourceTextHash;
    private String locationRefsJson;
    private String styleFingerprintJson;
    private Integer maxChars;
    private Integer maxLines;
    private String requiredEvidencePolicy;
    private String unsupportedRegionsJson;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
