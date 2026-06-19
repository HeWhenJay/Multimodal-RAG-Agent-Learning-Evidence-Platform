package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
@Builder
public class RagQueryTaskVO {

    private String taskId;
    private String status;
    private String message;
    private List<RagProgressVO> progressEvents;
    private RagQueryVO result;
    private String errorMessage;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
