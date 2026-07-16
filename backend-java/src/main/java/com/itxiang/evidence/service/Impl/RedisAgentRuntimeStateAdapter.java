package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.mapper.AgentCacheRepairTaskMapper;
import com.itxiang.evidence.service.AgentRuntimeStateAdapter;
import com.itxiang.evidence.vo.AgentChatMessageVO;
import com.itxiang.evidence.vo.AgentContextRestoreVO;
import com.itxiang.evidence.vo.AgentConversationSummaryVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Slf4j
@Component
@Primary
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.agent.redis", name = "enabled", havingValue = "true", matchIfMissing = true)
public class RedisAgentRuntimeStateAdapter implements AgentRuntimeStateAdapter {

    private static final TypeReference<List<AgentChatMessageVO>> MESSAGE_LIST_TYPE = new TypeReference<>() {
    };
    private static final TypeReference<List<Map<String, Object>>> EVENT_LIST_TYPE = new TypeReference<>() {
    };

    private final StringRedisTemplate redisTemplate;
    private final AgentProperties agentProperties;
    private final ObjectMapper objectMapper;
    private final AgentCacheRepairTaskMapper agentCacheRepairTaskMapper;

    @Override
    public Optional<AgentContextRestoreVO> readContext(String userId, String taskId) {
        try {
            if (agentCacheRepairTaskMapper.hasPending(taskId)) {
                log.debug("Agent 上下文缓存存在待修复删除任务，改从 PostgreSQL 恢复: taskId={}", taskId);
                return Optional.empty();
            }
            String contextJson = redisTemplate.opsForValue().get(contextKey(userId, taskId));
            String messagesJson = redisTemplate.opsForValue().get(messageKey(userId, taskId));
            if (contextJson == null || contextJson.isBlank() || messagesJson == null || messagesJson.isBlank()) {
                return Optional.empty();
            }
            AgentContextRestoreVO context = objectMapper.readValue(contextJson, AgentContextRestoreVO.class);
            List<AgentChatMessageVO> messages = objectMapper.readValue(messagesJson, MESSAGE_LIST_TYPE);
            if (context.getTaskId() == null || context.getUserId() == null || messages == null || messages.isEmpty()) {
                return Optional.empty();
            }
            Map<String, Object> metadata = new LinkedHashMap<>(context.getBudgetMetadata() == null ? Map.of() : context.getBudgetMetadata());
            metadata.put("restoreSource", "redis");
            metadata.put("redisPolicy", "Redis 仅作 L2 热态缓存；结构不完整或 TTL 过期时回源 PostgreSQL");
            context.setBudgetMetadata(metadata);
            context.setMessageWindow(messages);
            return Optional.of(context);
        } catch (Exception e) {
            log.warn("读取 Agent Redis 热态上下文失败，将回源 PostgreSQL: taskId={}, message={}", taskId, e.getMessage());
            return Optional.empty();
        }
    }

    @Override
    public void writeContext(AgentContextRestoreVO context, boolean terminal) {
        if (context == null || context.getTaskId() == null || context.getUserId() == null) {
            return;
        }
        try {
            // 先清除旧的成对缓存，避免第二次 set 失败时组合出新旧不一致的上下文。
            redisTemplate.delete(List.of(contextKey(context.getUserId(), context.getTaskId()), messageKey(context.getUserId(), context.getTaskId())));
            Map<String, Object> metadata = new LinkedHashMap<>(context.getBudgetMetadata() == null ? Map.of() : context.getBudgetMetadata());
            metadata.put("restoreSource", "postgresql_backfilled_to_redis");
            metadata.put("lastCompressionAt", metadata.getOrDefault("lastCompressionAt", ""));
            AgentContextRestoreVO cached = AgentContextRestoreVO.builder()
                    .taskId(context.getTaskId())
                    .userId(context.getUserId())
                    .messageWindow(List.of())
                    .compressionCandidateMessages(context.getCompressionCandidateMessages())
                    .activeSummaries(context.getActiveSummaries())
                    .summarySegments(context.getSummarySegments())
                    .budgetMetadata(metadata)
                    .build();
            Duration contextTtl = contextTtl(terminal);
            redisTemplate.opsForValue().set(contextKey(context.getUserId(), context.getTaskId()), objectMapper.writeValueAsString(cached), contextTtl);
            redisTemplate.opsForValue().set(messageKey(context.getUserId(), context.getTaskId()), objectMapper.writeValueAsString(context.getMessageWindow() == null ? List.of() : context.getMessageWindow()), messageTtl());
        } catch (Exception e) {
            log.warn("回填 Agent Redis 热态上下文失败: taskId={}, message={}", context.getTaskId(), e.getMessage());
        }
    }

    @Override
    public void updateMessage(String userId, String taskId, AgentChatMessageVO message, boolean terminal) {
        if (message == null) {
            return;
        }
        try {
            String key = messageKey(userId, taskId);
            List<AgentChatMessageVO> messages = readMessages(key);
            Map<String, AgentChatMessageVO> merged = new LinkedHashMap<>();
            for (AgentChatMessageVO item : messages) {
                if (item.getId() != null) {
                    merged.put(item.getId(), item);
                }
            }
            merged.put(message.getId(), message);
            List<AgentChatMessageVO> recent = merged.values().stream()
                    .sorted((left, right) -> Long.compare(sequence(left), sequence(right)))
                    .skip(Math.max(0, merged.size() - 40))
                    .toList();
            redisTemplate.opsForValue().set(key, objectMapper.writeValueAsString(recent), messageTtl());
            touchContextMetadata(userId, taskId, terminal, Map.of(
                    "recentMessageIds", recent.stream().map(AgentChatMessageVO::getId).toList(),
                    "restoreSource", "redis_hot_message_update"
            ));
        } catch (Exception e) {
            log.debug("更新 Agent Redis 最近消息失败: taskId={}, message={}", taskId, e.getMessage());
        }
    }

    @Override
    public void updateSummary(String userId, String taskId, AgentConversationSummaryVO summary, boolean terminal) {
        if (summary == null) {
            return;
        }
        try {
            touchContextMetadata(userId, taskId, terminal, Map.of(
                    "activeSummaryId", summary.getId(),
                    "lastCompressionAt", OffsetDateTime.now().toString(),
                    "restoreSource", "redis_summary_update"
            ));
        } catch (Exception e) {
            log.debug("更新 Agent Redis 摘要热态失败: taskId={}, message={}", taskId, e.getMessage());
        }
    }

    /**
     * 删除可从 PostgreSQL 重建的上下文和消息缓存；失败由持久化修复任务继续处理。
     */
    @Override
    public boolean invalidateContext(String userId, String taskId) {
        try {
            redisTemplate.delete(List.of(contextKey(userId, taskId), messageKey(userId, taskId)));
            return true;
        } catch (Exception e) {
            log.warn("删除 Agent Redis 上下文缓存失败，将由修复任务重试: taskId={}, message={}", taskId, e.getMessage());
            return false;
        }
    }

    @Override
    public void appendSseEvent(String taskId, Map<String, Object> event) {
        if (event == null || event.isEmpty()) {
            return;
        }
        try {
            String key = sseKey(taskId);
            long sequence = redisTemplate.opsForValue().increment(sequenceKey(taskId));
            if (sequence <= 0) {
                sequence = System.currentTimeMillis();
            }
            List<Map<String, Object>> events = readEvents(key);
            Map<String, Object> storedEvent = new LinkedHashMap<>(event);
            storedEvent.put("bufferSequence", sequence);
            events.add(storedEvent);
            int maxEvents = Math.max(20, agentProperties.getRedis().getSseMaxEvents());
            if (events.size() > maxEvents) {
                events = new ArrayList<>(events.subList(events.size() - maxEvents, events.size()));
            }
            redisTemplate.opsForValue().set(key, objectMapper.writeValueAsString(events), sseTtl());
            redisTemplate.expire(sequenceKey(taskId), sseTtl());
        } catch (Exception e) {
            log.debug("写入 Agent Redis SSE 缓冲失败: taskId={}, message={}", taskId, e.getMessage());
        }
    }

    @Override
    public List<Map<String, Object>> readSseEvents(String taskId) {
        try {
            return readEvents(sseKey(taskId));
        } catch (Exception e) {
            log.debug("读取 Agent Redis SSE 缓冲失败: taskId={}, message={}", taskId, e.getMessage());
            return List.of();
        }
    }

    @Override
    public void refreshTaskTtl(String userId, String taskId, boolean terminal) {
        try {
            redisTemplate.expire(contextKey(userId, taskId), contextTtl(terminal));
            redisTemplate.expire(messageKey(userId, taskId), messageTtl());
            redisTemplate.expire(sseKey(taskId), sseTtl());
            redisTemplate.expire(sequenceKey(taskId), sseTtl());
        } catch (Exception e) {
            log.debug("刷新 Agent Redis TTL 失败: taskId={}, message={}", taskId, e.getMessage());
        }
    }

    @Override
    public boolean enabled() {
        return Boolean.TRUE.equals(agentProperties.getRedis().getEnabled());
    }

    private void touchContextMetadata(String userId, String taskId, boolean terminal, Map<String, Object> updates) throws Exception {
        String key = contextKey(userId, taskId);
        AgentContextRestoreVO context;
        String json = redisTemplate.opsForValue().get(key);
        if (json == null || json.isBlank()) {
            context = AgentContextRestoreVO.builder()
                    .taskId(taskId)
                    .userId(userId)
                    .messageWindow(List.of())
                    .compressionCandidateMessages(List.of())
                    .activeSummaries(List.of())
                    .summarySegments(List.of())
                    .budgetMetadata(new LinkedHashMap<>())
                    .build();
        } else {
            context = objectMapper.readValue(json, AgentContextRestoreVO.class);
        }
        Map<String, Object> metadata = new LinkedHashMap<>(context.getBudgetMetadata() == null ? Map.of() : context.getBudgetMetadata());
        metadata.putAll(updates);
        context.setBudgetMetadata(metadata);
        redisTemplate.opsForValue().set(key, objectMapper.writeValueAsString(context), contextTtl(terminal));
    }

    private List<AgentChatMessageVO> readMessages(String key) throws Exception {
        String json = redisTemplate.opsForValue().get(key);
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        return new ArrayList<>(objectMapper.readValue(json, MESSAGE_LIST_TYPE));
    }

    private List<Map<String, Object>> readEvents(String key) throws Exception {
        String json = redisTemplate.opsForValue().get(key);
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        return new ArrayList<>(objectMapper.readValue(json, EVENT_LIST_TYPE));
    }

    private long sequence(AgentChatMessageVO message) {
        return message.getSequenceNo() == null ? 0L : message.getSequenceNo();
    }

    private Duration contextTtl(boolean terminal) {
        AgentProperties.Redis redis = agentProperties.getRedis();
        if (terminal) {
            return Duration.ofDays(Math.max(1, redis.getCompletedContextTtlDays()));
        }
        return Duration.ofHours(Math.max(1, redis.getRunningContextTtlHours()));
    }

    private Duration messageTtl() {
        return Duration.ofDays(Math.max(1, agentProperties.getRedis().getMessageTtlDays()));
    }

    private Duration sseTtl() {
        return Duration.ofHours(Math.max(1, agentProperties.getRedis().getSseTtlHours()));
    }

    private String contextKey(String userId, String taskId) {
        return "agent:ctx:" + userId + ":" + taskId;
    }

    private String messageKey(String userId, String taskId) {
        return "agent:ctx:messages:" + userId + ":" + taskId;
    }

    private String sseKey(String taskId) {
        return "agent:sse:" + taskId;
    }

    private String sequenceKey(String taskId) {
        return "agent:sse:seq:" + taskId;
    }
}
