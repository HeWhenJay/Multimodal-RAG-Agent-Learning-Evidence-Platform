package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.service.Impl.RagKafkaDeadLetterPublisher;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.apache.kafka.common.TopicPartition;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;

import java.util.concurrent.CompletableFuture;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RagKafkaDeadLetterPublisherTests {

    @Test
    void consumerFailurePublishesRedactedDlqEnvelope() {
        KafkaTemplate<String, String> kafkaTemplate = mock(KafkaTemplate.class);
        CompletableFuture<SendResult<String, String>> future = CompletableFuture.completedFuture(
                new SendResult<>(
                        new ProducerRecord<>("rag.material.index.dlq.v1", "material-88", "{}"),
                        new RecordMetadata(new TopicPartition("rag.material.index.dlq.v1", 0), 0, 0, 0L, 0, 0)
                )
        );
        when(kafkaTemplate.send(eq("rag.material.index.dlq.v1"), eq("material-88"), contains("RAG_KAFKA_CONSUMER_DLQ")))
                .thenReturn(future);
        RagKafkaDeadLetterPublisher publisher = new RagKafkaDeadLetterPublisher(
                new RagKafkaProperties(),
                kafkaTemplate,
                new ObjectMapper().findAndRegisterModules()
        );
        ConsumerRecord<String, String> record = new ConsumerRecord<>(
                "rag.material.index.result.v1",
                1,
                7L,
                "material-88",
                "{\"messageId\":\"source-1\",\"messageType\":\"RAG_INDEX_RESULT\",\"payload\":{\"jobId\":\"job-88\",\"materialId\":88,\"canonicalDocumentId\":\"material-88\",\"text\":\"资料正文\"}}"
        );

        publisher.publishConsumerFailure(record, new IllegalArgumentException("结果格式错误"));

        ArgumentCaptor<String> payloadCaptor = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(eq("rag.material.index.dlq.v1"), eq("material-88"), payloadCaptor.capture());
        org.assertj.core.api.Assertions.assertThat(payloadCaptor.getValue())
                .contains("RAG_KAFKA_CONSUMER_DLQ")
                .doesNotContain("资料正文");
    }

    @Test
    void failedDlqMessageIsNotSentToItselfAgain() {
        RagKafkaDeadLetterPublisher publisher = new RagKafkaDeadLetterPublisher(
                new RagKafkaProperties(),
                mock(KafkaTemplate.class),
                new ObjectMapper().findAndRegisterModules()
        );
        ConsumerRecord<String, String> record = new ConsumerRecord<>("rag.material.index.dlq.v1", 0, 1L, "key", "{}");

        assertThatThrownBy(() -> publisher.publishConsumerFailure(record, new IllegalArgumentException("DLQ 写库失败")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("保留原 offset");
    }
}
