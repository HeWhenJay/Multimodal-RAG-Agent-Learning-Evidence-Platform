package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.service.AgentRuntimeStateAdapter;
import com.itxiang.evidence.vo.AgentChatMessageVO;
import com.itxiang.evidence.vo.AgentContextRestoreVO;
import com.itxiang.evidence.vo.AgentConversationSummaryVO;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Optional;

@Component
public class NoopAgentRuntimeStateAdapter implements AgentRuntimeStateAdapter {

    @Override
    public Optional<AgentContextRestoreVO> readContext(String userId, String taskId) {
        return Optional.empty();
    }

    @Override
    public void writeContext(AgentContextRestoreVO context, boolean terminal) {
    }

    @Override
    public void updateMessage(String userId, String taskId, AgentChatMessageVO message, boolean terminal) {
    }

    @Override
    public void updateSummary(String userId, String taskId, AgentConversationSummaryVO summary, boolean terminal) {
    }

    @Override
    public void appendSseEvent(String taskId, Map<String, Object> event) {
    }

    @Override
    public List<Map<String, Object>> readSseEvents(String taskId) {
        return List.of();
    }

    @Override
    public void refreshTaskTtl(String userId, String taskId, boolean terminal) {
    }

    @Override
    public boolean enabled() {
        return false;
    }
}
