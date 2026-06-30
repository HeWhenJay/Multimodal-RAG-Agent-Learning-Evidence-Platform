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
     * Agent Redis 运行态配置；关闭或连接异常时自动降级到 PostgreSQL。
     */
    private Redis redis = new Redis();

    /**
     * 获取最终可用的内部令牌，避免本地联调时 Java/Python 需要分别手工配置。
     */
    public String getInternalToken() {
        internalToken = AgentInternalTokenResolver.resolve(internalToken);
        return internalToken;
    }

    @Data
    public static class Redis {

        /**
         * 是否启用 Agent Redis 热态缓存和 SSE 事件缓冲。
         */
        private Boolean enabled = true;

        /**
         * 任务运行中上下文热态 TTL，默认 24 小时。
         */
        private Integer runningContextTtlHours = 24;

        /**
         * 任务完成后上下文热态 TTL，默认 7 天。
         */
        private Integer completedContextTtlDays = 7;

        /**
         * 最近消息热态 TTL，默认 7 天。
         */
        private Integer messageTtlDays = 7;

        /**
         * SSE 重连事件缓冲 TTL，默认 2 小时。
         */
        private Integer sseTtlHours = 2;

        /**
         * Redis 中每个任务最多保留的 SSE 事件数。
         */
        private Integer sseMaxEvents = 120;
    }
}
