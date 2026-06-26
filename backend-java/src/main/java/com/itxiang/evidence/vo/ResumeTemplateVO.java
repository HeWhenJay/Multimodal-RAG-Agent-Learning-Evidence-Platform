package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class ResumeTemplateVO {

    private String templateId;
    private Integer version;
    private String status;
    private String filename;
    private String currentFilePath;
    private String currentPublicUrl;
    private String fileType;
    private Integer fieldCount;
    private Integer unsupportedRegionCount;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
