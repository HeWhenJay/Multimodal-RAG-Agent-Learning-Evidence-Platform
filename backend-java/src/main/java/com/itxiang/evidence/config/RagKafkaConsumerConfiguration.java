package com.itxiang.evidence.config;

import com.itxiang.evidence.service.Impl.RagKafkaDeadLetterPublisher;
import lombok.RequiredArgsConstructor;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.listener.ContainerProperties;
import org.springframework.kafka.listener.ConsumerRecordRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

/**
 * RAG Kafka 消费端采用有限本地重试，最终以脱敏 envelope 进入 DLQ。
 */
@Configuration
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.rag.kafka", name = "enabled", havingValue = "true")
public class RagKafkaConsumerConfiguration {

    private final RagKafkaProperties properties;
    private final RagKafkaDeadLetterPublisher deadLetterPublisher;

    @Bean("ragKafkaListenerContainerFactory")
    public ConcurrentKafkaListenerContainerFactory<String, String> ragKafkaListenerContainerFactory(
            ConsumerFactory<String, String> consumerFactory) {
        ConcurrentKafkaListenerContainerFactory<String, String> factory = new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(consumerFactory);
        factory.getContainerProperties().setAckMode(ContainerProperties.AckMode.RECORD);
        ConsumerRecordRecoverer recoverer = deadLetterPublisher::publishConsumerFailure;
        int maxAttempts = Math.max(1, properties.getConsumer().getMaxAttempts());
        long retryDelayMs = Math.max(0L, properties.getConsumer().getRetryDelayMs());
        DefaultErrorHandler errorHandler = new DefaultErrorHandler(recoverer, new FixedBackOff(retryDelayMs, maxAttempts - 1L));
        errorHandler.addNotRetryableExceptions(IllegalArgumentException.class);
        errorHandler.setCommitRecovered(true);
        factory.setCommonErrorHandler(errorHandler);
        return factory;
    }
}
