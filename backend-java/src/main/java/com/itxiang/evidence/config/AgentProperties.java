package com.itxiang.evidence.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Data
@ConfigurationProperties(prefix = "evidence.agent")
public class AgentProperties {

    /**
     * Python Agent 调 Java 内部接口的共享令牌；未显式配置时使用本地共享文件自动兜底。
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

    /**
     * 获取最终可用的内部令牌，避免本地联调时 Java/Python 需要分别手工配置。
     */
    public String getInternalToken() {
        internalToken = AgentInternalTokenResolver.resolve(internalToken);
        return internalToken;
    }
}
