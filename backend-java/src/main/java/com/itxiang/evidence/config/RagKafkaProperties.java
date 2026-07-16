package com.itxiang.evidence.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 读取 RAG Kafka 索引流水线配置，默认关闭以保留本地 HTTP 旧链路。
 */
@Data
@ConfigurationProperties(prefix = "evidence.rag.kafka")
public class RagKafkaProperties {

    private boolean enabled = false;
    private String bootstrapServers = "127.0.0.1:9092";
    private String internalBaseUrl = "http://127.0.0.1:7080";
    private String internalToken = "";
    private String internalTokenFile = "";
    private Fallback fallback = new Fallback();
    private Topics topics = new Topics();
    private Groups groups = new Groups();
    private Outbox outbox = new Outbox();

    @Data
    public static class Fallback {
        private boolean enabled = true;
        private long healthCheckTimeoutMs = 1500L;
        private long publishTimeoutMs = 3000L;
    }

    @Data
    public static class Topics {
        private String indexRequest = "rag.material.index.request.v1";
        private String uploadFinalizeRequest = "rag.upload.finalize.request.v1";
        private String progress = "rag.material.index.progress.v1";
        private String indexResult = "rag.material.index.result.v1";
        private String promoteRequest = "rag.material.index.promote.request.v1";
        private String promoteResult = "rag.material.index.promote.result.v1";
        private String indexDlq = "rag.material.index.dlq.v1";
        private String uploadFinalizeDlq = "rag.upload.finalize.dlq.v1";
    }

    @Data
    public static class Groups {
        private String uploadFinalizers = "rag-java-upload-finalizers";
        private String progressWriters = "rag-java-progress-writers";
        private String resultWriters = "rag-java-result-writers";
        private String promoteResultWriters = "rag-java-promote-result-writers";
        private String dlqWriters = "rag-java-dlq-writers";
    }

    @Data
    public static class Outbox {
        private int batchSize = 50;
        private int leaseSeconds = 60;
        private long publishFixedDelayMs = 1000L;
        private int maxAttempts = 8;
    }
}
