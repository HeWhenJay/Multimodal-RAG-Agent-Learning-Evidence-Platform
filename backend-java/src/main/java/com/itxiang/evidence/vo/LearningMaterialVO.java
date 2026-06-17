package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
@Builder
public class LearningMaterialVO {

    private Long id;
    private String title;
    private String userId;
    private String documentType;
    private String source;
    private String status;
    private String parser;
    private String documentSummary;
    private Integer chunkCount;
    private String originalFilename;
    private String originalFilePath;
    private String storageType;
    private String objectKey;
    private String publicUrl;
    private RagProgressVO latestProgress;
    private List<RagProgressVO> progressEvents;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
