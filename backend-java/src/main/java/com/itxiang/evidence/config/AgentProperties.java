package com.itxiang.evidence.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Data
@ConfigurationProperties(prefix = "evidence.agent")
public class AgentProperties {

    /**
     * Python Agent 调 Java 内部接口的共享令牌；未配置时内部 Agent 接口一律拒绝。
     */
    private String internalToken = "";

    /**
     * Java 对外可回调地址，Python Agent 通过该地址访问 Java Tool Gateway 和 events。
     */
    private String javaBaseUrl = "http://127.0.0.1:7080";

    /**
     * Java 请求 Python Agent 接收任务的超时时间。
     */
    private Integer startTimeoutSeconds = 10;
}
