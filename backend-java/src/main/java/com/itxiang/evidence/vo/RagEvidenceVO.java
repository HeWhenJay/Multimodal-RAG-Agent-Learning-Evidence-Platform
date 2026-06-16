package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class RagEvidenceVO {

    private String evidenceId;
    private String documentId;
    private String documentTitle;
    private String blockId;
    private String blockType;
    private Integer pageIndex;
    private Integer slideIndex;
    private String sheetName;
    private String cellRange;
    private String sectionTitle;
    private String title;
    private String snippet;
    private String source;
    private String sourcePath;
    private String assetPath;
    private String sectionName;
    private String documentType;
    private Double score;
    private String retrievalSource;
    private String parseEngine;
}
