package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class LogOverviewVO {

    private Long eventCount;
    private Long errorCount;
    private Long openErrorCount;
    private Long frontendErrorCount;
    private Long javaErrorCount;
    private Long pythonErrorCount;
}
