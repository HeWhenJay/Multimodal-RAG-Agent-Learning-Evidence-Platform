package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.service.RagKafkaAvailabilityProbe;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@Slf4j
@Service
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.rag.kafka", name = "enabled", havingValue = "true")
public class KafkaRagAvailabilityProbe implements RagKafkaAvailabilityProbe {

    private final RagKafkaProperties properties;

    /**
     * 使用短超时查询集群节点，避免 Kafka 故障拖慢资料上传接口。
     */
    @Override
    public boolean isAvailable() {
        int timeoutMs = timeoutMs();
        AdminClient adminClient = null;
        try {
            Map<String, Object> config = new HashMap<>();
            config.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, properties.getBootstrapServers());
            config.put(AdminClientConfig.REQUEST_TIMEOUT_MS_CONFIG, timeoutMs);
            config.put(AdminClientConfig.DEFAULT_API_TIMEOUT_MS_CONFIG, timeoutMs);
            adminClient = AdminClient.create(config);
            adminClient.describeCluster().nodes().get(timeoutMs, TimeUnit.MILLISECONDS);
            return true;
        } catch (Exception e) {
            log.debug("RAG Kafka 健康检查失败: bootstrapServers={}, reason={}",
                    properties.getBootstrapServers(), rootMessage(e));
            return false;
        } finally {
            if (adminClient != null) {
                adminClient.close(Duration.ofMillis(timeoutMs));
            }
        }
    }

    private int timeoutMs() {
        long configured = properties.getFallback().getHealthCheckTimeoutMs();
        return (int) Math.min(30_000L, Math.max(100L, configured));
    }

    private String rootMessage(Exception error) {
        Throwable current = error;
        while (current.getCause() != null) {
            current = current.getCause();
        }
        return current.getMessage() == null ? current.getClass().getSimpleName() : current.getMessage();
    }
}
