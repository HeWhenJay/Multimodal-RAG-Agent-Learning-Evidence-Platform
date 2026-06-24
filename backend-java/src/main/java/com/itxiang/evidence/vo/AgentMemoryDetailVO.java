package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data
@Builder
public class AgentMemoryDetailVO {

    private AgentMemoryVO memory;
    private List<AgentMemoryVersionVO> versions;
    private List<AgentMemoryAuditVO> audits;
}
