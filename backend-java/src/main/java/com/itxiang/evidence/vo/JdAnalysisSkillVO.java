package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class JdAnalysisSkillVO {

    private Long id;
    private String skillName;
    private String status;
}
