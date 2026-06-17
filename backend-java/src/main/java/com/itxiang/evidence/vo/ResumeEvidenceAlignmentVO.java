package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class ResumeEvidenceAlignmentVO {

    private Long id;
    private String userId;
    private String requirement;
    private String evidence;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
