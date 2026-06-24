package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class ResumeTemplate {

    private String id;
    private String userId;
    private String templateName;
    private String originalFilename;
    private String originalFilePath;
    private String storageType;
    private String objectKey;
    private String publicUrl;
    private String currentFilename;
    private String currentFilePath;
    private String currentStorageType;
    private String currentObjectKey;
    private String currentPublicUrl;
    private String fileType;
    private Integer version;
    private String status;
    private String layoutFingerprintJson;
    private String unsupportedRegionsJson;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
