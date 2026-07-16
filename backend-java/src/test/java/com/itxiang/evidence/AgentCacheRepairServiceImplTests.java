package com.itxiang.evidence;

import com.itxiang.evidence.mapper.AgentCacheRepairTaskMapper;
import com.itxiang.evidence.service.AgentRuntimeStateAdapter;
import com.itxiang.evidence.service.Impl.AgentCacheRepairServiceImpl;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentCacheRepairServiceImplTests {

    @Test
    void redisDeleteFailureKeepsDurableRetryTask() {
        AgentCacheRepairTaskMapper mapper = mock(AgentCacheRepairTaskMapper.class);
        AgentRuntimeStateAdapter runtimeState = mock(AgentRuntimeStateAdapter.class);
        when(runtimeState.invalidateContext("7", "agent-task-1")).thenReturn(false);
        AgentCacheRepairServiceImpl service = new AgentCacheRepairServiceImpl(
                mapper,
                runtimeState,
                transactionTemplate()
        );

        service.requestAfterCommit("7", "agent-task-1");

        verify(mapper).insertPending("7", "agent-task-1");
        verify(mapper).markRetry(eq("agent-task-1"), contains("未完成"), any());
    }

    @Test
    void redisDeleteSuccessResolvesRepairTask() {
        AgentCacheRepairTaskMapper mapper = mock(AgentCacheRepairTaskMapper.class);
        AgentRuntimeStateAdapter runtimeState = mock(AgentRuntimeStateAdapter.class);
        when(runtimeState.invalidateContext("7", "agent-task-1")).thenReturn(true);
        AgentCacheRepairServiceImpl service = new AgentCacheRepairServiceImpl(
                mapper,
                runtimeState,
                transactionTemplate()
        );

        service.requestAfterCommit("7", "agent-task-1");

        verify(mapper).markResolved("agent-task-1");
    }

    @Test
    void redisDeleteExceptionDoesNotPersistRawConnectionDetail() {
        AgentCacheRepairTaskMapper mapper = mock(AgentCacheRepairTaskMapper.class);
        AgentRuntimeStateAdapter runtimeState = mock(AgentRuntimeStateAdapter.class);
        when(runtimeState.invalidateContext("7", "agent-task-1"))
                .thenThrow(new IllegalStateException("redis://cache-user:password@127.0.0.1"));
        AgentCacheRepairServiceImpl service = new AgentCacheRepairServiceImpl(
                mapper,
                runtimeState,
                transactionTemplate()
        );

        service.requestAfterCommit("7", "agent-task-1");

        ArgumentCaptor<String> errorCaptor = ArgumentCaptor.forClass(String.class);
        verify(mapper).markRetry(eq("agent-task-1"), errorCaptor.capture(), any());
        assertThat(errorCaptor.getValue())
                .contains("IllegalStateException")
                .doesNotContain("password");
    }

    private TransactionTemplate transactionTemplate() {
        PlatformTransactionManager transactionManager = mock(PlatformTransactionManager.class);
        when(transactionManager.getTransaction(any())).thenReturn(new SimpleTransactionStatus());
        return new TransactionTemplate(transactionManager);
    }
}
