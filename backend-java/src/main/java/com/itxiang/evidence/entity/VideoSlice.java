package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class VideoSlice {

    private Long id;
    private String title;
    private String topic;
    private String startTime;
    private String endTime;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
