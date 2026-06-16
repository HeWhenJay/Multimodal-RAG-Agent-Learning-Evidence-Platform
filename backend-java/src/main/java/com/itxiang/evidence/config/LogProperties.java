package com.itxiang.evidence.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Data
@ConfigurationProperties(prefix = "evidence.logs")
public class LogProperties {

    private Boolean enabled = true;
    private Integer maxBatchSize = 50;
    private Integer maxContextBytes = 20480;
    private Integer maxStackTraceBytes = 20480;
    private Integer retentionDays = 30;
    private String internalToken = "";
}
