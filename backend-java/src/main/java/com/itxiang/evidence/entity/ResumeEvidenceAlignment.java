package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class ResumeEvidenceAlignment {

    private Long id;
    private String userId;
    private String requirement;
    private String evidence;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
