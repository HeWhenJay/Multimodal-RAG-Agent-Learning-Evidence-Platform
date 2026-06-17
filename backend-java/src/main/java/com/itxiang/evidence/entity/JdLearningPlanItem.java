package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class JdLearningPlanItem {

    private Long id;
    private Long reportId;
    private Integer stepNo;
    private String title;
    private String description;
    private LocalDateTime createdAt;
}
