package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class ResumeTemplateExport {

    private String id;
    private String templateId;
    private String userId;
    private Integer baseVersion;
    private Integer exportVersion;
    private String patchDraftId;
    private String filename;
    private String filePath;
    private String storageType;
    private String objectKey;
    private String publicUrl;
    private String layoutValidationJson;
    private String idempotencyKey;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
