package com.itsheng.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class RagOverviewVO {

    private Long materialCount;
    private Integer chunkCount;
    private Integer evidenceCount;
    private String lastIndexedTitle;
}

