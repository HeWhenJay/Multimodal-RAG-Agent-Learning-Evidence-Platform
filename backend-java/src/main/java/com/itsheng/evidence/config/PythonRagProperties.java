package com.itsheng.evidence.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Data
@ConfigurationProperties(prefix = "evidence.ai")
public class PythonRagProperties {

    private String pythonBaseUrl = "http://127.0.0.1:8090";
    private Integer queryTimeoutSeconds = 30;
    private Integer indexTimeoutSeconds = 60;
    private Integer overviewTimeoutSeconds = 5;
}

