package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class JdAnalysisReport {

    private Long id;
    private String userId;
    private String reportKey;
    private String jobDescription;
    private Integer matchScore;
    private Integer masteredPercent;
    private Integer partialPercent;
    private Integer gapPercent;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
