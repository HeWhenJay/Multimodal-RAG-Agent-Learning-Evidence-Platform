package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class ResumeTemplatePatchDraft {

    private String id;
    private String templateId;
    private String userId;
    private Integer templateVersion;
    private String status;
    private String jobDescriptionHash;
    private String patchesJson;
    private String evidenceCandidatesJson;
    private String validationErrorsJson;
    private String allowedFieldIdsJson;
    private Integer annotationRevision;
    private String provider;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
