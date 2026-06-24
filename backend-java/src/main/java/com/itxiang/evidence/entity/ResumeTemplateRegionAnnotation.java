package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class ResumeTemplateRegionAnnotation {

    private String id;
    private String templateId;
    private String userId;
    private Integer templateVersion;
    private String fieldId;
    private Integer pageIndex;
    private String rectJson;
    private String sourceType;
    private Boolean editable;
    private String sectionKey;
    private String userInstruction;
    private String requiredEvidencePolicy;
    private String status;
    private Integer annotationRevision;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
