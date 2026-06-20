package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class MaterialUploadChunkVO {

    private String uploadId;
    private String filename;
    private Integer chunkIndex;
    private Integer totalChunks;
    private Integer receivedChunks;
    private Integer nextChunkIndex;
    private String status;
    private String message;
    private Boolean completed;
    private LearningMaterialVO material;
}
