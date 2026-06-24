package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Data
@Builder
public class ResumePatchDraftVO {

    private String patchDraftId;
    private String templateId;
    private Integer version;
    private String status;
    private String provider;
    private List<Map<String, Object>> patches;
    private List<Map<String, Object>> evidenceCandidates;
    private List<String> validationErrors;
    private List<String> allowedFieldIds;
    private Integer annotationRevision;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
