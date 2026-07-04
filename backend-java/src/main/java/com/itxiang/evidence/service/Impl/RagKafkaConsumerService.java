package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagConsumedEvent;
import com.itxiang.evidence.entity.RagIndexJob;
import com.itxiang.evidence.entity.RagOutboxEvent;
import com.itxiang.evidence.kafka.RagKafkaEnvelope;
import com.itxiang.evidence.kafka.RagKafkaMessageFactory;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.RagConsumedEventMapper;
import com.itxiang.evidence.mapper.RagIndexJobMapper;
import com.itxiang.evidence.mapper.RagOutboxEventMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.command.RagUploadFinalizeCommand;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@Slf4j
@Service
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.rag.kafka", name = "enabled", havingValue = "true")
public class RagKafkaConsumerService {

    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    private final RagKafkaProperties properties;
    private final RagConsumedEventMapper ragConsumedEventMapper;
    private final RagIndexJobMapper ragIndexJobMapper;
    private final RagOutboxEventMapper ragOutboxEventMapper;
    private final LearningMaterialMapper learningMaterialMapper;
    private final RagUploadWorker ragUploadWorker;
    private final LogService logService;
    private final ObjectMapper objectMapper;
    private final KafkaTemplate<String, String> kafkaTemplate;

    /**
     * 消费 Python 进度事件并写入 log_event。
     */
    @KafkaListener(
            topics = "${evidence.rag.kafka.topics.progress:rag.material.index.progress.v1}",
            groupId = "${evidence.rag.kafka.groups.progress-writers:rag-java-progress-writers}"
    )
    @Transactional
    public void consumeProgress(String message) {
        RagKafkaEnvelope envelope = readEnvelope(message);
        Map<String, Object> payload = payload(envelope);
        if (!rememberConsumed("rag-java-progress-writers", envelope, text(payload, "jobId"), integer(payload, "progressSequence"))) {
            return;
        }
        String stageCode = text(payload, "stageCode");
        String messageText = defaultText(text(payload, "message"), "RAG 索引进度更新");
        Map<String, Object> context = new LinkedHashMap<>(payload);
        context.put("documentId", payload.get("canonicalDocumentId"));
        logService.recordRagProgress(
                "material",
                stageCode,
                "kafka_progress_" + sanitizeAction(stageCode),
                messageText,
                context,
                !"FAILED".equals(text(payload, "status"))
        );
    }

    /**
     * 消费 Python staging 索引结果，只有 active job 才能进入 promote。
     */
    @KafkaListener(
            topics = "${evidence.rag.kafka.topics.index-result:rag.material.index.result.v1}",
            groupId = "${evidence.rag.kafka.groups.result-writers:rag-java-result-writers}"
    )
    @Transactional
    public void consumeIndexResult(String message) {
        RagKafkaEnvelope envelope = readEnvelope(message);
        Map<String, Object> payload = payload(envelope);
        String jobId = text(payload, "jobId");
        if (!rememberConsumed("rag-java-result-writers", envelope, jobId, null)) {
            return;
        }
        Long materialId = longValue(payload.get("materialId"));
        LearningMaterial material = materialId == null ? null : learningMaterialMapper.findById(materialId);
        RagIndexJob job = jobId == null ? null : ragIndexJobMapper.findById(jobId);
        if (!isActive(material, job, integer(payload, "requestVersion"))) {
            markIgnored(job, "stale index result ignored");
            return;
        }
        if ("FAILED".equals(text(payload, "status"))) {
            String errorMessage = defaultText(text(payload, "errorMessage"), "Python staging 索引失败");
            ragIndexJobMapper.markFinished(jobId, "FAILED", toJson(payload), text(payload, "errorCode"), truncate(errorMessage, 1000));
            learningMaterialMapper.updateIndexResult(material.getId(), "FAILED", "python-rag-error", truncate(errorMessage, 500), 0);
            learningMaterialMapper.clearActiveIndexJob(material.getId(), jobId);
            return;
        }
        ragIndexJobMapper.markIndexed(jobId, "INDEXED", toJson(payload), null, null);
        publishPromoteRequest(material, job, payload);
    }

    /**
     * 消费 Python promote 结果，只有 promote 成功后才更新资料终态。
     */
    @KafkaListener(
            topics = "${evidence.rag.kafka.topics.promote-result:rag.material.index.promote.result.v1}",
            groupId = "${evidence.rag.kafka.groups.promote-result-writers:rag-java-promote-result-writers}"
    )
    @Transactional
    public void consumePromoteResult(String message) {
        RagKafkaEnvelope envelope = readEnvelope(message);
        Map<String, Object> payload = payload(envelope);
        String jobId = text(payload, "jobId");
        if (!rememberConsumed("rag-java-promote-result-writers", envelope, jobId, null)) {
            return;
        }
        Long materialId = longValue(payload.get("materialId"));
        LearningMaterial material = materialId == null ? null : learningMaterialMapper.findById(materialId);
        RagIndexJob job = jobId == null ? null : ragIndexJobMapper.findById(jobId);
        if (!isActive(material, job, integer(payload, "requestVersion"))) {
            markIgnored(job, "stale promote result ignored");
            return;
        }
        if (!"SUCCEEDED".equals(text(payload, "status"))) {
            String errorMessage = defaultText(text(payload, "errorMessage"), "Python promote 失败");
            ragIndexJobMapper.markFinished(jobId, "FAILED", toJson(payload), text(payload, "errorCode"), truncate(errorMessage, 1000));
            learningMaterialMapper.updateIndexResult(material.getId(), "FAILED", "python-rag-promote-error", truncate(errorMessage, 500), 0);
            learningMaterialMapper.clearActiveIndexJob(material.getId(), jobId);
            return;
        }
        Map<String, Object> indexResult = readJsonObject(job.getResultJson());
        String finalStatus = defaultText(text(indexResult, "status"), "READY");
        String parser = text(indexResult, "parser");
        String summary = text(indexResult, "documentSummary");
        Integer chunkCount = integer(indexResult, "chunkCount");
        learningMaterialMapper.updateIndexResult(material.getId(), finalStatus, parser, summary, chunkCount == null ? 0 : chunkCount);
        learningMaterialMapper.clearActiveIndexJob(material.getId(), jobId);
        ragIndexJobMapper.markFinished(jobId, "SUCCEEDED", toJson(payload), null, null);
    }

    /**
     * 消费 Kafka 分片收尾请求，执行 Java 本地合并和对象存储上传。
     */
    @KafkaListener(
            topics = "${evidence.rag.kafka.topics.upload-finalize-request:rag.upload.finalize.request.v1}",
            groupId = "${evidence.rag.kafka.groups.upload-finalizers:rag-java-upload-finalizers}"
    )
    @Transactional
    public void consumeUploadFinalize(String message) {
        RagKafkaEnvelope envelope = readEnvelope(message);
        Map<String, Object> payload = payload(envelope);
        if (!rememberConsumed("rag-java-upload-finalizers", envelope, null, null)) {
            return;
        }
        try {
            ragUploadWorker.completeChunkedUploadForKafka(
                    longValue(payload.get("materialId")),
                    text(payload, "userId"),
                    Path.of(text(payload, "directory")),
                    Path.of(text(payload, "chunkRoot")),
                    text(payload, "uploadId"),
                    text(payload, "filename"),
                    text(payload, "contentType"),
                    integer(payload, "totalChunks"),
                    longValue(payload.get("totalSize")),
                    bool(payload.get("highPrecision"))
            );
        } catch (Exception e) {
            publishUploadFinalizeDlq(envelope, payload, e);
        }
    }

    /**
     * 记录 DLQ 摘要，禁止记录文档正文。
     */
    @KafkaListener(
            topics = {
                    "${evidence.rag.kafka.topics.index-dlq:rag.material.index.dlq.v1}",
                    "${evidence.rag.kafka.topics.upload-finalize-dlq:rag.upload.finalize.dlq.v1}"
            },
            groupId = "${evidence.rag.kafka.groups.dlq-writers:rag-java-dlq-writers}"
    )
    @Transactional
    public void consumeDlq(String message) {
        RagKafkaEnvelope envelope = readEnvelope(message);
        Map<String, Object> payload = payload(envelope);
        if (!rememberConsumed("rag-java-dlq-writers", envelope, text(payload, "jobId"), null)) {
            return;
        }
        Map<String, Object> context = new LinkedHashMap<>();
        for (String key : new String[]{"jobId", "materialId", "canonicalDocumentId", "requestVersion", "attempt", "topic", "errorCode", "errorMessage"}) {
            if (payload.containsKey(key)) {
                context.put(key, payload.get(key));
            }
        }
        logService.recordRagError(
                "material",
                "kafka.dlq",
                "rag_kafka_dlq_received",
                defaultText(text(payload, "errorCode"), "RAG_KAFKA_DLQ"),
                "RAG Kafka 消息进入 DLQ",
                null,
                context
        );
    }

    /**
     * 发布 promote 请求到 Outbox。
     */
    private void publishPromoteRequest(LearningMaterial material, RagIndexJob job, Map<String, Object> indexResult) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("jobId", job.getId());
        payload.put("materialId", material.getId());
        payload.put("canonicalDocumentId", job.getCanonicalDocumentId());
        payload.put("stagingDocumentId", job.getStagingDocumentId());
        payload.put("requestVersion", job.getRequestVersion());
        payload.put("chunkCount", integer(indexResult, "chunkCount"));
        String idempotencyKey = RagKafkaMessageFactory.promoteIdempotencyKey(job.getCanonicalDocumentId(), job.getId());
        RagKafkaEnvelope promote = RagKafkaMessageFactory.envelope(
                "RAG_PROMOTE_REQUESTED",
                "backend-java",
                job.getCanonicalDocumentId(),
                idempotencyKey,
                0,
                payload
        );
        RagOutboxEvent event = new RagOutboxEvent();
        event.setTopic(properties.getTopics().getPromoteRequest());
        event.setMessageKey(job.getCanonicalDocumentId());
        event.setEventType("RAG_PROMOTE_REQUESTED");
        event.setIdempotencyKey(idempotencyKey);
        event.setPayloadJson(toJson(promote));
        event.setStatus("NEW");
        event.setAttempt(0);
        event.setNextAttemptAt(LocalDateTime.now());
        ragOutboxEventMapper.insert(event);
    }

    /**
     * 同步发送分片收尾 DLQ；发送失败会抛出，避免 Kafka listener 提交原 offset。
     */
    private void publishUploadFinalizeDlq(RagKafkaEnvelope sourceEnvelope, Map<String, Object> payload, Exception error) {
        String uploadId = defaultText(text(payload, "uploadId"), "unknown-upload");
        Long materialId = longValue(payload.get("materialId"));
        Map<String, Object> dlqPayload = new LinkedHashMap<>();
        dlqPayload.put("uploadId", uploadId);
        dlqPayload.put("materialId", materialId);
        dlqPayload.put("filename", text(payload, "filename"));
        dlqPayload.put("attempt", sourceEnvelope.getAttempt());
        dlqPayload.put("topic", properties.getTopics().getUploadFinalizeRequest());
        dlqPayload.put("errorCode", "RAG_UPLOAD_FINALIZE_FAILED");
        dlqPayload.put("errorMessage", truncate(error.getMessage(), 500));
        String idempotencyKey = "RAG_UPLOAD_FINALIZE_DLQ:%s:%s:%s:v1".formatted(uploadId, materialId, sourceEnvelope.getAttempt());
        RagKafkaEnvelope dlq = RagKafkaMessageFactory.envelope(
                "RAG_UPLOAD_FINALIZE_DLQ",
                "backend-java",
                uploadId,
                idempotencyKey,
                sourceEnvelope.getAttempt(),
                dlqPayload
        );
        try {
            kafkaTemplate.send(properties.getTopics().getUploadFinalizeDlq(), uploadId, toJson(dlq)).get(10, TimeUnit.SECONDS);
        } catch (Exception sendError) {
            throw new IllegalStateException("RAG 分片收尾 DLQ 发送失败: " + sendError.getMessage(), sendError);
        }
    }

    private boolean rememberConsumed(String consumerName, RagKafkaEnvelope envelope, String jobId, Integer progressSequence) {
        RagConsumedEvent event = new RagConsumedEvent();
        event.setConsumerName(consumerName);
        event.setMessageId(envelope.getMessageId());
        event.setMessageType(envelope.getMessageType());
        event.setIdempotencyKey(envelope.getIdempotencyKey());
        event.setJobId(jobId);
        event.setProgressSequence(progressSequence);
        event.setStatus("CONSUMED");
        return ragConsumedEventMapper.insertIgnore(event) > 0;
    }

    private boolean isActive(LearningMaterial material, RagIndexJob job, Integer requestVersion) {
        return material != null
                && job != null
                && job.getId().equals(material.getActiveIndexJobId())
                && requestVersion != null
                && requestVersion.equals(material.getIndexRequestVersion())
                && requestVersion.equals(job.getRequestVersion());
    }

    private void markIgnored(RagIndexJob job, String reason) {
        if (job != null) {
            ragIndexJobMapper.markIgnored(job.getId(), "STALE_IGNORED", reason);
        }
    }

    private RagKafkaEnvelope readEnvelope(String message) {
        try {
            return objectMapper.readValue(message, RagKafkaEnvelope.class);
        } catch (Exception e) {
            throw new IllegalArgumentException("RAG Kafka 消息 envelope 解析失败: " + e.getMessage(), e);
        }
    }

    private Map<String, Object> payload(RagKafkaEnvelope envelope) {
        return envelope.getPayload() == null ? Map.of() : envelope.getPayload();
    }

    private Map<String, Object> readJsonObject(String json) {
        if (json == null || json.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(json, MAP_TYPE);
        } catch (Exception e) {
            return Map.of();
        }
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("RAG Kafka 消息序列化失败: " + e.getMessage(), e);
        }
    }

    private String text(Map<String, Object> map, String key) {
        Object value = map.get(key);
        return value == null ? null : String.valueOf(value);
    }

    private Integer integer(Map<String, Object> map, String key) {
        Object value = map.get(key);
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return value == null ? null : Integer.parseInt(String.valueOf(value));
        } catch (Exception e) {
            return null;
        }
    }

    private Long longValue(Object value) {
        if (value instanceof Number number) {
            return number.longValue();
        }
        try {
            return value == null ? null : Long.parseLong(String.valueOf(value));
        } catch (Exception e) {
            return null;
        }
    }

    private Boolean bool(Object value) {
        if (value instanceof Boolean bool) {
            return bool;
        }
        return value != null && Boolean.parseBoolean(String.valueOf(value));
    }

    private String defaultText(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private String sanitizeAction(String value) {
        return value == null ? "unknown" : value.replaceAll("[^A-Za-z0-9_]+", "_");
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
