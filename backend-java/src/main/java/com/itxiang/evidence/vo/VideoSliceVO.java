package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class VideoSliceVO {

    private Long id;
    private String title;
    private String topic;
    private String startTime;
    private String endTime;
    private String status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
