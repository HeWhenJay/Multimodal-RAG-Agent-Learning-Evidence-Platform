package com.itxiang.evidence.kafka;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.UUID;

/**
 * 构造 RAG Kafka 统一 envelope 和幂等键。
 */
public final class RagKafkaMessageFactory {

    private RagKafkaMessageFactory() {
    }

    /**
     * 创建标准 Kafka envelope。
     */
    public static RagKafkaEnvelope envelope(String messageType,
                                            String producer,
                                            String partitionKey,
                                            String idempotencyKey,
                                            Integer attempt,
                                            Map<String, Object> payload) {
        RagKafkaEnvelope envelope = new RagKafkaEnvelope();
        String messageId = UUID.randomUUID().toString();
        envelope.setMessageId(messageId);
        envelope.setOriginalMessageId(messageId);
        envelope.setMessageType(messageType);
        envelope.setEventTime(OffsetDateTime.now(ZoneOffset.UTC));
        envelope.setProducer(producer);
        envelope.setTraceId("rag_" + UUID.randomUUID().toString().replace("-", ""));
        envelope.setCorrelationId(partitionKey);
        envelope.setPartitionKey(partitionKey);
        envelope.setIdempotencyKey(idempotencyKey);
        envelope.setAttempt(attempt == null ? 0 : attempt);
        envelope.setPayload(payload);
        return envelope;
    }

    public static String indexIdempotencyKey(String canonicalDocumentId, String jobId) {
        return "RAG_INDEX:%s:%s:v1".formatted(canonicalDocumentId, jobId);
    }

    public static String promoteIdempotencyKey(String canonicalDocumentId, String jobId) {
        return "RAG_PROMOTE:%s:%s:v1".formatted(canonicalDocumentId, jobId);
    }

    public static String uploadFinalizeIdempotencyKey(String uploadId, Long materialId) {
        return "RAG_UPLOAD_FINALIZE:%s:%s:v1".formatted(uploadId, materialId);
    }
}
