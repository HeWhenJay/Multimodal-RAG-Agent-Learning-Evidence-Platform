package com.itsheng.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class RagEvidenceVO {

    private String evidenceId;
    private String documentId;
    private String title;
    private String snippet;
    private String source;
    private String sectionName;
    private String documentType;
    private Double score;
}

