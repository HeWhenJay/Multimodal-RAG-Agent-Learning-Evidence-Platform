package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagIndexJob;
import com.itxiang.evidence.entity.RagOutboxEvent;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.RagIndexJobMapper;
import com.itxiang.evidence.mapper.RagOutboxEventMapper;
import com.itxiang.evidence.service.RagKafkaAvailabilityProbe;
import com.itxiang.evidence.service.Impl.KafkaRagIndexTaskPublisher;
import com.itxiang.evidence.service.Impl.RagHttpFallbackTaskDispatcher;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class KafkaRagIndexTaskPublisherTests {

    @Mock
    private LearningMaterialMapper learningMaterialMapper;

    @Mock
    private RagIndexJobMapper ragIndexJobMapper;

    @Mock
    private RagOutboxEventMapper ragOutboxEventMapper;

    @Mock
    private RagKafkaAvailabilityProbe availabilityProbe;

    @Mock
    private RagHttpFallbackTaskDispatcher fallbackTaskDispatcher;

    private KafkaRagIndexTaskPublisher publisher;

    @BeforeEach
    void setUp() {
        publisher = new KafkaRagIndexTaskPublisher(
                new RagKafkaProperties(),
                learningMaterialMapper,
                ragIndexJobMapper,
                ragOutboxEventMapper,
                new ObjectMapper().findAndRegisterModules(),
                availabilityProbe,
                fallbackTaskDispatcher
        );
    }

    @Test
    void unavailableKafkaUsesHttpFallbackWithoutCreatingOutbox() {
        LearningMaterial material = material(41L);
        when(availabilityProbe.isAvailable()).thenReturn(false);

        publisher.publishStoredMaterialIndex(material, "7", true, "INDEX_UPLOAD");

        verify(fallbackTaskDispatcher).publishStoredMaterialIndex(material, "7", true, "INDEX_UPLOAD");
        verify(learningMaterialMapper, never()).updateActiveIndexJob(any(), any(), any(), any());
        verify(ragIndexJobMapper, never()).insert(any());
        verify(ragOutboxEventMapper, never()).insert(any());
    }

    @Test
    void availableKafkaCreatesJobAndOutboxForTextIndex() {
        LearningMaterial material = material(42L);
        material.setIndexRequestVersion(3);
        RagIndexTextDTO dto = new RagIndexTextDTO();
        dto.setContent("Kafka 可用时应写入 Outbox");
        when(availabilityProbe.isAvailable()).thenReturn(true);

        publisher.publishTextIndex(material, "7", dto);

        ArgumentCaptor<RagIndexJob> jobCaptor = ArgumentCaptor.forClass(RagIndexJob.class);
        ArgumentCaptor<RagOutboxEvent> outboxCaptor = ArgumentCaptor.forClass(RagOutboxEvent.class);
        verify(learningMaterialMapper).updateActiveIndexJob(eq(42L), any(), eq(4), eq("PARSING"));
        verify(ragIndexJobMapper).insert(jobCaptor.capture());
        verify(ragOutboxEventMapper).insert(outboxCaptor.capture());
        verify(fallbackTaskDispatcher, never()).publishTextIndex(any(), any(), any());

        assertThat(jobCaptor.getValue().getStatus()).isEqualTo("REQUESTED");
        assertThat(jobCaptor.getValue().getRequestVersion()).isEqualTo(4);
        assertThat(outboxCaptor.getValue().getTopic()).isEqualTo("rag.material.index.request.v1");
        assertThat(outboxCaptor.getValue().getPayloadJson()).contains("RAG_INDEX_REQUESTED");
    }

    private LearningMaterial material(Long id) {
        LearningMaterial material = new LearningMaterial();
        material.setId(id);
        material.setTitle("Kafka 测试资料");
        material.setStatus("PARSING");
        material.setDocumentType("markdown");
        material.setSource("manual");
        material.setOriginalFilename("kafka-test.md");
        material.setStorageType("manual");
        return material;
    }
}
