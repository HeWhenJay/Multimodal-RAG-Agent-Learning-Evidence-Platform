package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class RagProgressVO {

    private String stageCode;
    private String stageLabel;
    private String message;
    private String status;
    private Integer currentStep;
    private Integer totalSteps;
    private Integer currentChunk;
    private Integer totalChunks;
    private String chunkId;
    private String blockId;
    private Integer percent;
    private String detail;
    private LocalDateTime createdAt;
}
