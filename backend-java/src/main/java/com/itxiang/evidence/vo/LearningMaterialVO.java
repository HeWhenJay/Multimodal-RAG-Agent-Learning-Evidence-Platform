package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class LearningMaterialVO {

    private Long id;
    private String title;
    private String documentType;
    private String source;
    private String status;
    private String parser;
    private String documentSummary;
    private Integer chunkCount;
    private String originalFilename;
    private String originalFilePath;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
