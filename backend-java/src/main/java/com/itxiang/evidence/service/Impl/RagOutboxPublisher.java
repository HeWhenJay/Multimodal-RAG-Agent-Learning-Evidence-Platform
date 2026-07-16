package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.entity.RagOutboxEvent;
import com.itxiang.evidence.mapper.RagOutboxEventMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.net.InetAddress;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

@Slf4j
@Service
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.rag.kafka", name = "enabled", havingValue = "true")
public class RagOutboxPublisher {

    private final RagKafkaProperties properties;
    private final RagOutboxEventMapper ragOutboxEventMapper;
    private final KafkaTemplate<String, String> kafkaTemplate;
    private final String publisherId = buildPublisherId();

    /**
     * 定时发布到期 Outbox 事件，Kafka 关闭时该 bean 不会创建。
     */
    @Scheduled(fixedDelayString = "${evidence.rag.kafka.outbox.publish-fixed-delay-ms:1000}")
    @Transactional
    public void publishDueEvents() {
        LocalDateTime now = LocalDateTime.now();
        List<RagOutboxEvent> events = ragOutboxEventMapper.findDueForPublish(now, properties.getOutbox().getBatchSize());
        for (RagOutboxEvent event : events) {
            LocalDateTime leaseUntil = now.plusSeconds(properties.getOutbox().getLeaseSeconds());
            if (ragOutboxEventMapper.lease(event.getId(), publisherId, leaseUntil) <= 0) {
                continue;
            }
            publishOne(event);
        }
    }

    /**
     * 发布单条事件，成功后标记 PUBLISHED，失败后设置重试时间。
     */
    private void publishOne(RagOutboxEvent event) {
        try {
            kafkaTemplate.send(event.getTopic(), event.getMessageKey(), event.getPayloadJson())
                    .get(publishTimeoutMs(), TimeUnit.MILLISECONDS);
            ragOutboxEventMapper.markPublished(event.getId());
        } catch (Exception e) {
            LocalDateTime nextAttempt = LocalDateTime.now().plusSeconds(backoffSeconds(event.getAttempt()));
            ragOutboxEventMapper.markFailed(event.getId(), truncate(e.getMessage(), 1000), nextAttempt);
            log.warn("RAG Outbox 发布失败: id={}, topic={}, reason={}", event.getId(), event.getTopic(), e.getMessage());
        }
    }

    private long backoffSeconds(Integer attempt) {
        int safeAttempt = attempt == null ? 1 : Math.max(1, attempt);
        return Math.min(3600, (long) Math.pow(2, Math.min(safeAttempt, properties.getOutbox().getMaxAttempts())));
    }

    private long publishTimeoutMs() {
        return Math.max(100L, properties.getFallback().getPublishTimeoutMs());
    }

    private String buildPublisherId() {
        try {
            return InetAddress.getLocalHost().getHostName() + "-" + UUID.randomUUID();
        } catch (Exception e) {
            return "unknown-" + UUID.randomUUID();
        }
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
