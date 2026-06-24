package com.itxiang.evidence.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Data
@ConfigurationProperties(prefix = "evidence.tools.tavily")
public class TavilyProperties {

    /**
     * Tavily Search API Key，未配置时联网参考工具返回可恢复错误。
     */
    private String apiKey = "";

    /**
     * Tavily API 基础地址。
     */
    private String baseUrl = "https://api.tavily.com";

    /**
     * Tavily 查询超时时间。
     */
    private Integer timeoutSeconds = 15;
}
