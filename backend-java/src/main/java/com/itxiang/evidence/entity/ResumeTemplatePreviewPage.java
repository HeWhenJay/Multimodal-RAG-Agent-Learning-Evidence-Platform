package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class ResumeTemplatePreviewPage {

    private String id;
    private String templateId;
    private String userId;
    private Integer templateVersion;
    private Integer pageIndex;
    private String storageType;
    private String filePath;
    private String objectKey;
    private Integer width;
    private Integer height;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
