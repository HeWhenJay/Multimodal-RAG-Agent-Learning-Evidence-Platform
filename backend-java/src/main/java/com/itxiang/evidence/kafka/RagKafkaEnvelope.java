package com.itxiang.evidence.kafka;

import lombok.Data;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

@Data
public class RagKafkaEnvelope {

    private String schemaVersion = "1.0";
    private String messageId;
    private String originalMessageId;
    private String messageType;
    private OffsetDateTime eventTime;
    private String producer;
    private String traceId;
    private String correlationId;
    private String partitionKey;
    private String idempotencyKey;
    private Integer attempt = 0;
    private OffsetDateTime notBefore;
    private Map<String, Object> payload = new LinkedHashMap<>();
}
