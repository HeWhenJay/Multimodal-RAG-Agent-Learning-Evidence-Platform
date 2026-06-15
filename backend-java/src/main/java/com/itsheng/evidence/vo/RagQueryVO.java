package com.itsheng.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data
@Builder
public class RagQueryVO {

    private String answer;
    private List<String> expandedQueries;
    private List<RagEvidenceVO> evidences;
}

