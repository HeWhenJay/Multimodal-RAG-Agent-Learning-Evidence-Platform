package com.itxiang.evidence;

import com.itxiang.evidence.service.RagIndexTaskPublisher;
import com.itxiang.evidence.service.RagKafkaAvailabilityProbe;
import com.itxiang.evidence.service.AgentRuntimeStateAdapter;
import com.itxiang.evidence.service.Impl.KafkaRagIndexTaskPublisher;
import com.itxiang.evidence.service.Impl.RedisAgentRuntimeStateAdapter;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.kafka.test.context.EmbeddedKafka;

import static org.assertj.core.api.Assertions.assertThat;

@EmbeddedKafka(
        partitions = 1,
        bootstrapServersProperty = "spring.kafka.bootstrap-servers",
        topics = {
                "rag.material.index.request.v1",
                "rag.upload.finalize.request.v1",
                "rag.material.index.progress.v1",
                "rag.material.index.result.v1",
                "rag.material.index.promote.request.v1",
                "rag.material.index.promote.result.v1",
                "rag.material.index.retry.1m.v1",
                "rag.material.index.retry.10m.v1",
                "rag.material.index.retry.1h.v1",
                "rag.material.index.dlq.v1",
                "rag.upload.finalize.dlq.v1"
        }
)
@SpringBootTest(properties = {
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.url=jdbc:h2:mem:kafka-enabled-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.sql.init.mode=always",
        "evidence.agent.internal-token=agent-secret",
        "evidence.rag.kafka.enabled=true",
        "evidence.rag.kafka.bootstrap-servers=${spring.kafka.bootstrap-servers}",
        "evidence.rag.kafka.fallback.health-check-timeout-ms=5000"
})
class KafkaEnabledApplicationIntegrationTests {

    @Autowired
    private RagIndexTaskPublisher ragIndexTaskPublisher;

    @Autowired
    private RagKafkaAvailabilityProbe kafkaAvailabilityProbe;

    @Autowired
    private AgentRuntimeStateAdapter agentRuntimeStateAdapter;

    @Test
    void kafkaModeUsesKafkaPublisherAndDetectsEmbeddedBroker() {
        assertThat(ragIndexTaskPublisher).isInstanceOf(KafkaRagIndexTaskPublisher.class);
        assertThat(kafkaAvailabilityProbe.isAvailable()).isTrue();
        assertThat(agentRuntimeStateAdapter).isInstanceOf(RedisAgentRuntimeStateAdapter.class);
    }
}
