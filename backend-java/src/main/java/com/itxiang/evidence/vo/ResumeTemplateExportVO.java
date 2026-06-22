package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.Map;

@Data
@Builder
public class ResumeTemplateExportVO {

    private String exportId;
    private String templateId;
    private Integer baseVersion;
    private Integer exportVersion;
    private String patchDraftId;
    private String filename;
    private String filePath;
    private String storageType;
    private String publicUrl;
    private String status;
    private Map<String, Object> layoutValidation;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
