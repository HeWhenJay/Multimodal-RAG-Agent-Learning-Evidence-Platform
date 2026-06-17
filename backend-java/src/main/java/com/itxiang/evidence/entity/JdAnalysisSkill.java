package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class JdAnalysisSkill {

    private Long id;
    private Long reportId;
    private String skillName;
    private String status;
    private LocalDateTime createdAt;
}
