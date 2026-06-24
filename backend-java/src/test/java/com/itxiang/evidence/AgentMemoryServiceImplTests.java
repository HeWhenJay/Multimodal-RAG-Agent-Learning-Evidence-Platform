package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonMemoryClient;
import com.itxiang.evidence.dto.AgentMemoryPatchDTO;
import com.itxiang.evidence.entity.AgentMemoryItem;
import com.itxiang.evidence.entity.AgentTask;
import com.itxiang.evidence.mapper.AgentMemoryAuditMapper;
import com.itxiang.evidence.mapper.AgentMemoryItemMapper;
import com.itxiang.evidence.mapper.AgentMemoryVersionMapper;
import com.itxiang.evidence.service.Impl.AgentMemoryServiceImpl;
import com.itxiang.evidence.vo.AgentMemoryVO;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AgentMemoryServiceImplTests {

    @Mock
    private AgentMemoryItemMapper memoryItemMapper;

    @Mock
    private AgentMemoryVersionMapper memoryVersionMapper;

    @Mock
    private AgentMemoryAuditMapper memoryAuditMapper;

    @Mock
    private PythonMemoryClient pythonMemoryClient;

    private ObjectMapper objectMapper;
    private AgentMemoryServiceImpl service;

    @BeforeEach
    void setUp() {
        objectMapper = new ObjectMapper();
        service = new AgentMemoryServiceImpl(
                memoryItemMapper,
                memoryVersionMapper,
                memoryAuditMapper,
                pythonMemoryClient,
                objectMapper
        );
    }

    @Test
    void pendingReviewMemoryDoesNotEnterMemoryContext() throws Exception {
        AgentTask task = task("agent-task-1", "7");
        when(pythonMemoryClient.query(any())).thenReturn(objectMapper.readTree("""
                {"memories":[{"memoryId":"agent-memory-1","score":0.91}]}
                """));
        when(memoryItemMapper.findByIdAndUserId("agent-memory-1", "7")).thenReturn(memory("agent-memory-1", "7", "PENDING_REVIEW", "USER", null));
        when(memoryItemMapper.findActiveByUser("7")).thenReturn(List.of());

        List<Map<String, Object>> result = service.retrieveForTask(task, Map.of("query", "Redis"));

        assertThat(result).isEmpty();
        verify(memoryItemMapper, never()).markAccessed(any(), any());
    }

    @Test
    void archivedMemoryDoesNotEnterMemoryContext() throws Exception {
        AgentTask task = task("agent-task-1", "7");
        when(pythonMemoryClient.query(any())).thenReturn(objectMapper.readTree("""
                {"memories":[{"memoryId":"agent-memory-1","score":0.77}]}
                """));
        when(memoryItemMapper.findByIdAndUserId("agent-memory-1", "7")).thenReturn(memory("agent-memory-1", "7", "ARCHIVED", "USER", null));
        when(memoryItemMapper.findActiveByUser("7")).thenReturn(List.of());

        List<Map<String, Object>> result = service.retrieveForTask(task, Map.of("query", "Redis"));

        assertThat(result).isEmpty();
        verify(memoryItemMapper, never()).markAccessed(any(), any());
    }

    @Test
    void taskScopeCannotBeEnlargedToUserScope() {
        AgentMemoryItem oldItem = memory("agent-memory-1", "7", "ACTIVE", "TASK", "agent-task-1");
        when(memoryItemMapper.findByIdAndUserId("agent-memory-1", "7")).thenReturn(oldItem);
        AgentMemoryPatchDTO patch = new AgentMemoryPatchDTO();
        patch.setScopeType("USER");

        assertThatThrownBy(() -> service.patchMemory("agent-memory-1", patch, "7"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("AGENT_MEMORY_SCOPE_ESCALATION");

        verify(memoryItemMapper, never()).insert(any());
    }

    @Test
    void confirmedMemoryBecomesActiveWhenPythonIndexSucceeds() throws Exception {
        AgentMemoryItem item = memory("agent-memory-1", "7", "PENDING_REVIEW", "USER", null);
        when(memoryItemMapper.findByIdAndUserId("agent-memory-1", "7")).thenReturn(item, item);
        when(pythonMemoryClient.upsertIndex(any())).thenReturn(objectMapper.readTree("""
                {"memoryId":"agent-memory-1","indexed":true,"status":"ACTIVE"}
                """));

        AgentMemoryVO result = service.confirmMemory("agent-memory-1", "7");

        assertThat(result.getStatus()).isEqualTo("ACTIVE");
        verify(memoryItemMapper, atLeastOnce()).update(item);
        verify(pythonMemoryClient).upsertIndex(any());
    }

    @Test
    void activeFallbackMemoryCanBeRetrievedForSameTaskOwner() {
        AgentTask task = task("agent-task-1", "7");
        AgentMemoryItem active = memory("agent-memory-1", "7", "ACTIVE", "USER", null);
        when(pythonMemoryClient.query(any())).thenThrow(new IllegalStateException("Python 暂不可用"));
        when(memoryItemMapper.findActiveByUser("7")).thenReturn(List.of(active));

        List<Map<String, Object>> result = service.retrieveForTask(task, Map.of("query", "Redis 记忆"));

        assertThat(result).hasSize(1);
        assertThat(result.get(0).get("memoryId")).isEqualTo("agent-memory-1");
        verify(memoryItemMapper).markAccessed("agent-memory-1", "7");
    }

    /**
     * 构造测试任务。
     */
    private AgentTask task(String taskId, String userId) {
        AgentTask task = new AgentTask();
        task.setId(taskId);
        task.setUserId(userId);
        task.setTaskType("planning_task");
        task.setInputJson("{\"goal\":\"Redis 记忆检索\"}");
        task.setTitle("Redis 记忆检索");
        return task;
    }

    /**
     * 构造测试记忆。
     */
    private AgentMemoryItem memory(String id, String userId, String status, String scopeType, String scopeId) {
        AgentMemoryItem item = new AgentMemoryItem();
        item.setId(id);
        item.setUserId(userId);
        item.setMemoryType("PREFERENCE");
        item.setNamespace("user_preference");
        item.setScopeType(scopeType);
        item.setScopeId(scopeId);
        item.setSubjectKey("answer_style");
        item.setContent("用户希望 Redis 问答先讲缓存策略。");
        item.setSummary("Redis 问答先讲缓存策略。");
        item.setEvidenceRefsJson("[]");
        item.setStatus(status);
        item.setConfidence(BigDecimal.valueOf(0.8));
        item.setImportance(BigDecimal.valueOf(0.7));
        item.setSensitivityLevel("LOW");
        item.setConsentSource("USER_REVIEW");
        item.setSourceHash("hash-1");
        item.setAccessCount(0);
        return item;
    }
}
