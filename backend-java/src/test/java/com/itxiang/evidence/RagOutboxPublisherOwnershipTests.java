package com.itxiang.evidence;

import com.itxiang.evidence.service.Impl.RagOutboxPublisher;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.kafka.test.context.EmbeddedKafka;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 验证迁移到 Python cron 后，Java 不会与 Python 同时发布同一批 Outbox 事件。
 */
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
        "spring.datasource.url=jdbc:h2:mem:kafka-outbox-owner-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.sql.init.mode=always",
        "evidence.agent.internal-token=agent-secret",
        "evidence.rag.kafka.enabled=true",
        "evidence.rag.kafka.bootstrap-servers=${spring.kafka.bootstrap-servers}",
        "evidence.rag.kafka.outbox.publisher-enabled=false"
})
class RagOutboxPublisherOwnershipTests {

    @Autowired
    private ApplicationContext applicationContext;

    @Test
    void pythonOutboxOwnerDisablesJavaScheduledPublisher() {
        assertThat(applicationContext.getBeansOfType(RagOutboxPublisher.class)).isEmpty();
    }
}
