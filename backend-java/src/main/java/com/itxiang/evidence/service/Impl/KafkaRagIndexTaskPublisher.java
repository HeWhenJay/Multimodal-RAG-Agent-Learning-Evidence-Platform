package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagIndexJob;
import com.itxiang.evidence.entity.RagOutboxEvent;
import com.itxiang.evidence.kafka.RagKafkaEnvelope;
import com.itxiang.evidence.kafka.RagKafkaMessageFactory;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.RagIndexJobMapper;
import com.itxiang.evidence.mapper.RagOutboxEventMapper;
import com.itxiang.evidence.service.RagIndexTaskPublisher;
import com.itxiang.evidence.service.command.RagUploadFinalizeCommand;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.rag.kafka", name = "enabled", havingValue = "true")
public class KafkaRagIndexTaskPublisher implements RagIndexTaskPublisher {

    private final RagKafkaProperties properties;
    private final LearningMaterialMapper learningMaterialMapper;
    private final RagIndexJobMapper ragIndexJobMapper;
    private final RagOutboxEventMapper ragOutboxEventMapper;
    private final ObjectMapper objectMapper;

    @Override
    public boolean kafkaEnabled() {
        return true;
    }

    /**
     * 为已保存资料创建索引任务和 Outbox 请求。
     */
    @Override
    public void publishStoredMaterialIndex(LearningMaterial material, String userId, Boolean highPrecision, String operation) {
        String jobId = newJobId();
        String canonicalDocumentId = canonicalDocumentId(material.getId());
        Integer requestVersion = nextRequestVersion(material);
        String stagingDocumentId = stagingDocumentId(canonicalDocumentId, jobId);
        String idempotencyKey = RagKafkaMessageFactory.indexIdempotencyKey(canonicalDocumentId, jobId);
        Map<String, Object> sourceRef = new LinkedHashMap<>();
        sourceRef.put("type", "JAVA_SOURCE_API");
        sourceRef.put("javaBaseUrl", properties.getInternalBaseUrl());
        sourceRef.put("downloadPath", "/api/internal/rag/materials/%s/source?jobId=%s&requestVersion=%s".formatted(material.getId(), jobId, requestVersion));
        sourceRef.put("filename", material.getOriginalFilename());
        sourceRef.put("contentType", null);
        sourceRef.put("storageType", material.getStorageType());
        sourceRef.put("sourcePath", material.getOriginalFilePath());
        sourceRef.put("objectKey", material.getObjectKey());
        sourceRef.put("publicUrl", material.getPublicUrl());
        publishIndexRequest(material, userId, operation, highPrecision, jobId, canonicalDocumentId, stagingDocumentId, requestVersion, idempotencyKey, sourceRef, null);
    }

    /**
     * 为手动文本资料创建 Kafka 索引任务。
     */
    @Override
    public void publishTextIndex(LearningMaterial material, String userId, RagIndexTextDTO dto) {
        String jobId = newJobId();
        String canonicalDocumentId = canonicalDocumentId(material.getId());
        Integer requestVersion = nextRequestVersion(material);
        String stagingDocumentId = stagingDocumentId(canonicalDocumentId, jobId);
        String idempotencyKey = RagKafkaMessageFactory.indexIdempotencyKey(canonicalDocumentId, jobId);
        Map<String, Object> sourceRef = new LinkedHashMap<>();
        sourceRef.put("type", "INLINE_TEXT");
        sourceRef.put("parser", "java-manual-text");
        publishIndexRequest(material, userId, "INDEX_TEXT", false, jobId, canonicalDocumentId, stagingDocumentId, requestVersion, idempotencyKey, sourceRef, dto.getContent());
    }

    /**
     * 为分片上传创建 Kafka 收尾任务。
     */
    @Override
    public void publishUploadFinalize(RagUploadFinalizeCommand command) {
        String idempotencyKey = RagKafkaMessageFactory.uploadFinalizeIdempotencyKey(command.uploadId(), command.materialId());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("materialId", command.materialId());
        payload.put("userId", command.userId());
        payload.put("directory", command.directory().toString());
        payload.put("chunkRoot", command.chunkRoot().toString());
        payload.put("uploadId", command.uploadId());
        payload.put("filename", command.filename());
        payload.put("contentType", command.contentType());
        payload.put("totalChunks", command.totalChunks());
        payload.put("totalSize", command.totalSize());
        payload.put("highPrecision", Boolean.TRUE.equals(command.highPrecision()));
        RagKafkaEnvelope envelope = RagKafkaMessageFactory.envelope(
                "RAG_UPLOAD_FINALIZE_REQUESTED",
                "backend-java",
                command.uploadId(),
                idempotencyKey,
                0,
                payload
        );
        insertOutbox(properties.getTopics().getUploadFinalizeRequest(), command.uploadId(), "RAG_UPLOAD_FINALIZE_REQUESTED", idempotencyKey, toJson(envelope));
    }

    /**
     * 写入索引任务、更新资料 active job，并把 request envelope 放入 Outbox。
     */
    private void publishIndexRequest(LearningMaterial material,
                                     String userId,
                                     String operation,
                                     Boolean highPrecision,
                                     String jobId,
                                     String canonicalDocumentId,
                                     String stagingDocumentId,
                                     Integer requestVersion,
                                     String idempotencyKey,
                                     Map<String, Object> sourceRef,
                                     String text) {
        learningMaterialMapper.updateActiveIndexJob(material.getId(), jobId, requestVersion, material.getStatus());
        material.setActiveIndexJobId(jobId);
        material.setIndexRequestVersion(requestVersion);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("jobId", jobId);
        payload.put("operation", operation);
        payload.put("materialId", material.getId());
        payload.put("canonicalDocumentId", canonicalDocumentId);
        payload.put("stagingDocumentId", stagingDocumentId);
        payload.put("userId", userId);
        payload.put("title", material.getTitle());
        payload.put("documentType", material.getDocumentType());
        payload.put("source", material.getSource());
        payload.put("visibilityScope", "private");
        payload.put("stagingVisibilityScope", "staging");
        payload.put("highPrecision", Boolean.TRUE.equals(highPrecision));
        payload.put("requestVersion", requestVersion);
        payload.put("sourceRef", sourceRef);
        if (text != null) {
            payload.put("text", text);
        }

        RagIndexJob job = new RagIndexJob();
        job.setId(jobId);
        job.setMaterialId(material.getId());
        job.setCanonicalDocumentId(canonicalDocumentId);
        job.setStagingDocumentId(stagingDocumentId);
        job.setUserId(userId);
        job.setOperation(operation);
        job.setStatus("REQUESTED");
        job.setRequestVersion(requestVersion);
        job.setIdempotencyKey(idempotencyKey);
        job.setAttempt(0);
        job.setRequestJson(toJson(payload));
        job.setResultJson("{}");
        ragIndexJobMapper.insert(job);

        RagKafkaEnvelope envelope = RagKafkaMessageFactory.envelope(
                "RAG_INDEX_REQUESTED",
                "backend-java",
                canonicalDocumentId,
                idempotencyKey,
                0,
                payload
        );
        insertOutbox(properties.getTopics().getIndexRequest(), canonicalDocumentId, "RAG_INDEX_REQUESTED", idempotencyKey, toJson(envelope));
    }

    /**
     * 写入 Outbox 事件。
     */
    private void insertOutbox(String topic, String key, String eventType, String idempotencyKey, String payloadJson) {
        RagOutboxEvent event = new RagOutboxEvent();
        event.setTopic(topic);
        event.setMessageKey(key);
        event.setEventType(eventType);
        event.setIdempotencyKey(idempotencyKey);
        event.setPayloadJson(payloadJson);
        event.setStatus("NEW");
        event.setAttempt(0);
        event.setNextAttemptAt(LocalDateTime.now());
        ragOutboxEventMapper.insert(event);
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("RAG Kafka 消息序列化失败: " + e.getMessage(), e);
        }
    }

    private Integer nextRequestVersion(LearningMaterial material) {
        return (material.getIndexRequestVersion() == null ? 0 : material.getIndexRequestVersion()) + 1;
    }

    private String newJobId() {
        return "job_" + UUID.randomUUID().toString().replace("-", "");
    }

    private String canonicalDocumentId(Long materialId) {
        return "material-" + materialId;
    }

    private String stagingDocumentId(String canonicalDocumentId, String jobId) {
        return canonicalDocumentId + "__job-" + jobId;
    }
}
