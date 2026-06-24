package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

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
    private List<Map<String, Object>> fields;
    private List<String> unsupportedRegions;
    private Map<String, Object> layoutFingerprint;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
