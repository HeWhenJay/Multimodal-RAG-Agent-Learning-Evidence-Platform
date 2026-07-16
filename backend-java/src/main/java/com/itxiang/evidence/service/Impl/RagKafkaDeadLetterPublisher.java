package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.kafka.RagKafkaEnvelope;
import com.itxiang.evidence.kafka.RagKafkaMessageFactory;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * 将 Java 消费端的不可恢复错误转换为脱敏 DLQ envelope。
 */
@Slf4j
@Service
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.rag.kafka", name = "enabled", havingValue = "true")
public class RagKafkaDeadLetterPublisher {

    private final RagKafkaProperties properties;
    private final KafkaTemplate<String, String> kafkaTemplate;
    private final ObjectMapper objectMapper;

    /**
     * 发送消费失败摘要；DLQ 自身失败不再递归投递，保留原 offset 等待人工或依赖恢复。
     */
    public void publishConsumerFailure(ConsumerRecord<?, ?> record, Exception error) {
        if (isDlqTopic(record.topic())) {
            throw new IllegalStateException("DLQ 消息消费失败，保留原 offset: topic=" + record.topic(), error);
        }
        String rawValue = recordValue(record.value());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("topic", record.topic());
        payload.put("sourceTopic", record.topic());
        payload.put("partition", record.partition());
        payload.put("offset", record.offset());
        payload.put("attempt", 0);
        payload.put("errorCode", "RAG_KAFKA_CONSUMER_FAILED");
        payload.put("errorMessage", safeErrorSummary(error));
        payload.put("messageHash", sha256(rawValue));
        appendSafeSourceFields(rawValue, payload);

        String partitionKey = text(payload, "canonicalDocumentId");
        if (partitionKey == null || partitionKey.isBlank()) {
            partitionKey = defaultText(text(payload, "jobId"), record.topic() + "-" + record.partition());
        }
        String idempotencyKey = "RAG_CONSUMER_DLQ:%s:%s:%s:v1".formatted(record.topic(), record.partition(), record.offset());
        RagKafkaEnvelope envelope = RagKafkaMessageFactory.envelope(
                "RAG_KAFKA_CONSUMER_DLQ",
                "backend-java",
                partitionKey,
                idempotencyKey,
                0,
                payload
        );
        String targetTopic = uploadFinalizeTopic(record.topic())
                ? properties.getTopics().getUploadFinalizeDlq()
                : properties.getTopics().getIndexDlq();
        try {
            kafkaTemplate.send(targetTopic, partitionKey, objectMapper.writeValueAsString(envelope))
                    .get(publishTimeoutMs(), TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            throw new IllegalStateException("RAG Kafka 消费失败消息写入 DLQ 失败: " + safeErrorSummary(e), e);
        }
    }

    /**
     * 从原 envelope 仅保留定位和状态字段，不写入资料正文或敏感来源信息。
     */
    private void appendSafeSourceFields(String rawValue, Map<String, Object> target) {
        try {
            RagKafkaEnvelope source = objectMapper.readValue(rawValue, RagKafkaEnvelope.class);
            target.put("sourceMessageId", source.getMessageId());
            target.put("sourceMessageType", source.getMessageType());
            target.put("sourceIdempotencyKey", source.getIdempotencyKey());
            Map<String, Object> payload = source.getPayload() == null ? Map.of() : source.getPayload();
            for (String key : new String[]{"jobId", "materialId", "canonicalDocumentId", "stagingDocumentId", "requestVersion", "uploadId"}) {
                if (payload.containsKey(key)) {
                    target.put(key, payload.get(key));
                }
            }
        } catch (Exception ignored) {
            // 原消息无法解析时仅保留 topic、offset 和哈希，避免把原文复制到 DLQ。
        }
    }

    private boolean isDlqTopic(String topic) {
        return properties.getTopics().getIndexDlq().equals(topic)
                || properties.getTopics().getUploadFinalizeDlq().equals(topic);
    }

    private boolean uploadFinalizeTopic(String topic) {
        return properties.getTopics().getUploadFinalizeRequest().equals(topic);
    }

    private long publishTimeoutMs() {
        return Math.max(100L, properties.getFallback().getPublishTimeoutMs());
    }

    private String recordValue(Object value) {
        if (value instanceof byte[] bytes) {
            return new String(bytes, StandardCharsets.UTF_8);
        }
        return value == null ? "" : String.valueOf(value);
    }

    private String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder builder = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                builder.append(String.format("%02x", item));
            }
            return builder.toString();
        } catch (Exception e) {
            return "hash-unavailable";
        }
    }

    private String safeErrorSummary(Exception error) {
        Throwable current = error;
        while (current.getCause() != null) {
            current = current.getCause();
        }
        return "Kafka 消费异常：" + current.getClass().getSimpleName();
    }

    private String defaultText(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private String text(Map<String, Object> map, String key) {
        Object value = map.get(key);
        return value == null ? null : String.valueOf(value);
    }

}
