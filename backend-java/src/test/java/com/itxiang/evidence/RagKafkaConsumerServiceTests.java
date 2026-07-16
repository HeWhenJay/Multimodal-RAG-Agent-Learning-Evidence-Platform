package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagConsumedEvent;
import com.itxiang.evidence.entity.RagIndexJob;
import com.itxiang.evidence.kafka.RagKafkaMessageFactory;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.RagConsumedEventMapper;
import com.itxiang.evidence.mapper.RagIndexJobMapper;
import com.itxiang.evidence.mapper.RagOutboxEventMapper;
import com.itxiang.evidence.service.Impl.RagKafkaConsumerService;
import com.itxiang.evidence.service.Impl.RagUploadWorker;
import com.itxiang.evidence.service.LogService;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.apache.kafka.common.TopicPartition;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RagKafkaConsumerServiceTests {

    private RagConsumedEventMapper consumedEventMapper;
    private RagIndexJobMapper indexJobMapper;
    private RagOutboxEventMapper outboxEventMapper;
    private LearningMaterialMapper learningMaterialMapper;
    private LogService logService;
    private RagUploadWorker ragUploadWorker;
    private KafkaTemplate<String, String> kafkaTemplate;
    private RagKafkaConsumerService service;
    private ObjectMapper objectMapper;

    @BeforeEach
    void setUp() {
        consumedEventMapper = mock(RagConsumedEventMapper.class);
        indexJobMapper = mock(RagIndexJobMapper.class);
        outboxEventMapper = mock(RagOutboxEventMapper.class);
        learningMaterialMapper = mock(LearningMaterialMapper.class);
        logService = mock(LogService.class);
        ragUploadWorker = mock(RagUploadWorker.class);
        kafkaTemplate = mock(KafkaTemplate.class);
        objectMapper = new ObjectMapper().findAndRegisterModules();
        service = new RagKafkaConsumerService(
                new RagKafkaProperties(),
                consumedEventMapper,
                indexJobMapper,
                outboxEventMapper,
                learningMaterialMapper,
                ragUploadWorker,
                logService,
                objectMapper,
                kafkaTemplate
        );
    }

    @Test
    void progressIdempotencyKeepsDifferentSequenceValues() throws Exception {
        when(consumedEventMapper.insertIgnore(any(RagConsumedEvent.class))).thenReturn(1);
        service.consumeProgress(envelopeJson("RAG_INDEX_PROGRESS", "RAG_PROGRESS:material-1:job-1:1:v1", Map.of(
                "jobId", "job-1",
                "materialId", 1,
                "canonicalDocumentId", "material-1",
                "stagingDocumentId", "material-1__job-job-1",
                "requestVersion", 1,
                "progressSequence", 1,
                "stageCode", "embedding.chunk",
                "message", "第 1 块",
                "status", "RUNNING"
        )));
        service.consumeProgress(envelopeJson("RAG_INDEX_PROGRESS", "RAG_PROGRESS:material-1:job-1:2:v1", Map.of(
                "jobId", "job-1",
                "materialId", 1,
                "canonicalDocumentId", "material-1",
                "stagingDocumentId", "material-1__job-job-1",
                "requestVersion", 1,
                "progressSequence", 2,
                "stageCode", "embedding.chunk",
                "message", "第 2 块",
                "status", "RUNNING"
        )));
        ArgumentCaptor<RagConsumedEvent> captor = ArgumentCaptor.forClass(RagConsumedEvent.class);

        verify(consumedEventMapper, org.mockito.Mockito.times(2)).insertIgnore(captor.capture());

        assertThat(captor.getAllValues()).extracting(RagConsumedEvent::getProgressSequence).containsExactly(1, 2);
    }

    @Test
    void staleIndexResultDoesNotPromoteOrUpdateMaterial() throws Exception {
        when(consumedEventMapper.insertIgnore(any(RagConsumedEvent.class))).thenReturn(1);
        LearningMaterial material = new LearningMaterial();
        material.setId(88L);
        material.setActiveIndexJobId("job-new");
        material.setIndexRequestVersion(2);
        RagIndexJob oldJob = new RagIndexJob();
        oldJob.setId("job-old");
        oldJob.setMaterialId(88L);
        oldJob.setRequestVersion(1);
        when(learningMaterialMapper.findById(eq(88L))).thenReturn(material);
        when(indexJobMapper.findById(eq("job-old"))).thenReturn(oldJob);

        service.consumeIndexResult(envelopeJson("RAG_INDEX_RESULT", "RAG_INDEX_RESULT:material-88:job-old:v1", Map.of(
                "jobId", "job-old",
                "materialId", 88,
                "canonicalDocumentId", "material-88",
                "stagingDocumentId", "material-88__job-job-old",
                "requestVersion", 1,
                "status", "READY",
                "parser", "unit-parser",
                "chunkCount", 3,
                "documentSummary", "旧结果"
        )));

        verify(indexJobMapper).markIgnored(eq("job-old"), eq("STALE_IGNORED"), eq("过期索引结果已忽略"));
        verify(outboxEventMapper, never()).insert(any());
        verify(learningMaterialMapper, never()).updateIndexResult(any(), any(), any(), any(), any());
    }

    @Test
    void uploadFinalizeFailurePublishesDlqBeforeReturning() throws Exception {
        when(consumedEventMapper.insertIgnore(any(RagConsumedEvent.class))).thenReturn(1);
        doThrow(new IllegalStateException("分片缺失"))
                .when(ragUploadWorker)
                .completeChunkedUploadForKafka(any(), any(), any(), any(), any(), any(), any(), any(), any(), any());
        CompletableFuture<SendResult<String, String>> future = CompletableFuture.completedFuture(
                new SendResult<>(
                        new ProducerRecord<>("rag.upload.finalize.dlq.v1", "upload-1", "{}"),
                        new RecordMetadata(new TopicPartition("rag.upload.finalize.dlq.v1", 0), 0, 0, 0L, 0, 0)
                )
        );
        when(kafkaTemplate.send(eq("rag.upload.finalize.dlq.v1"), eq("upload-1"), contains("RAG_UPLOAD_FINALIZE_FAILED")))
                .thenReturn(future);

        service.consumeUploadFinalize(envelopeJson("RAG_UPLOAD_FINALIZE_REQUESTED", "RAG_UPLOAD_FINALIZE:upload-1:88:v1", Map.of(
                "uploadId", "upload-1",
                "materialId", 88,
                "userId", "7",
                "directory", "C:/tmp/upload-1",
                "chunkRoot", "C:/tmp",
                "filename", "course.mp4",
                "contentType", "video/mp4",
                "totalChunks", 3,
                "totalSize", 1024,
                "highPrecision", false
        )));

        verify(kafkaTemplate).send(eq("rag.upload.finalize.dlq.v1"), eq("upload-1"), contains("RAG_UPLOAD_FINALIZE_FAILED"));
    }

    @Test
    void terminalDlqMarksActiveJobAndMaterialFailed() throws Exception {
        when(consumedEventMapper.insertIgnore(any(RagConsumedEvent.class))).thenReturn(1);
        RagIndexJob job = new RagIndexJob();
        job.setId("job-88");
        job.setMaterialId(88L);
        LearningMaterial material = new LearningMaterial();
        material.setId(88L);
        material.setActiveIndexJobId("job-88");
        when(indexJobMapper.findById("job-88")).thenReturn(job);
        when(learningMaterialMapper.findById(88L)).thenReturn(material);

        Map<String, Object> dlqPayload = new LinkedHashMap<>();
        dlqPayload.put("jobId", "job-88");
        dlqPayload.put("materialId", 88);
        dlqPayload.put("canonicalDocumentId", "material-88");
        dlqPayload.put("topic", "rag.material.index.request.v1");
        dlqPayload.put("sourceTopic", "rag.material.index.request.v1");
        dlqPayload.put("partition", 0);
        dlqPayload.put("offset", 7L);
        dlqPayload.put("messageHash", "safe-message-hash");
        dlqPayload.put("request", "正文不应进入日志");
        dlqPayload.put("errorCode", "RAG_KAFKA_RETRY_EXHAUSTED");
        dlqPayload.put("errorMessage", "索引重试次数已耗尽");
        service.consumeDlq(envelopeJson("RAG_INDEX_DLQ", "RAG_DLQ:material-88:job-88:3:v1", dlqPayload));

        verify(indexJobMapper).markFinished(eq("job-88"), eq("DLQ"), any(), eq("RAG_KAFKA_RETRY_EXHAUSTED"), contains("重试次数已耗尽"));
        verify(learningMaterialMapper).updateIndexResult(eq(88L), eq("FAILED"), eq("kafka-dlq"), contains("重试次数已耗尽"), eq(0));
        verify(learningMaterialMapper).clearActiveIndexJob(88L, "job-88");
        ArgumentCaptor<String> messageCaptor = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<Map<String, Object>> contextCaptor = ArgumentCaptor.forClass(Map.class);
        verify(logService).recordRagError(
                eq("material"),
                eq("kafka.dlq"),
                eq("rag_kafka_dlq_received"),
                any(),
                messageCaptor.capture(),
                any(),
                contextCaptor.capture()
        );
        assertThat(messageCaptor.getValue())
                .startsWith("RAG Kafka 消息进入 DLQ（定位：")
                .doesNotContain("正文不应进入日志");
        assertThat(contextCaptor.getValue())
                .containsEntry("sourceTopic", "rag.material.index.request.v1")
                .containsEntry("partition", 0)
                .containsEntry("offset", 7)
                .containsEntry("messageHash", "safe-message-hash")
                .doesNotContainKey("request");
    }

    private String envelopeJson(String messageType, String idempotencyKey, Map<String, Object> payload) throws Exception {
        Map<String, Object> mutablePayload = new LinkedHashMap<>(payload);
        var envelope = RagKafkaMessageFactory.envelope(
                messageType,
                "unit-test",
                String.valueOf(mutablePayload.getOrDefault("canonicalDocumentId", "material-1")),
                idempotencyKey,
                0,
                mutablePayload
        );
        return objectMapper.writeValueAsString(envelope);
    }
}
