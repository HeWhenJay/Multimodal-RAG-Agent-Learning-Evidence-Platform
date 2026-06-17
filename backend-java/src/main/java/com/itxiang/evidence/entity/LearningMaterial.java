package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class LearningMaterial {

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
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
