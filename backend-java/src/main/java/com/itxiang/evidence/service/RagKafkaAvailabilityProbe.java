package com.itxiang.evidence.service;

/**
 * 检查当前 Java 服务是否可以安全提交新的 RAG Kafka 任务。
 */
public interface RagKafkaAvailabilityProbe {

    boolean isAvailable();
}
