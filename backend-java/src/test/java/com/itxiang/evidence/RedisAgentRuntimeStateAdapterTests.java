package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.mapper.AgentCacheRepairTaskMapper;
import com.itxiang.evidence.service.Impl.RedisAgentRuntimeStateAdapter;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class RedisAgentRuntimeStateAdapterTests {

    @Test
    void pendingRepairTaskBypassesRedisContext() {
        StringRedisTemplate redisTemplate = mock(StringRedisTemplate.class);
        AgentCacheRepairTaskMapper repairTaskMapper = mock(AgentCacheRepairTaskMapper.class);
        when(repairTaskMapper.hasPending("agent-task-1")).thenReturn(true);
        RedisAgentRuntimeStateAdapter adapter = new RedisAgentRuntimeStateAdapter(
                redisTemplate,
                new AgentProperties(),
                new ObjectMapper().findAndRegisterModules(),
                repairTaskMapper
        );

        assertThat(adapter.readContext("7", "agent-task-1")).isEmpty();

        verify(repairTaskMapper).hasPending("agent-task-1");
        verifyNoInteractions(redisTemplate);
    }
}
