package com.itxiang.evidence.service;

import com.itxiang.evidence.vo.AgentChatMessageVO;
import com.itxiang.evidence.vo.AgentContextRestoreVO;
import com.itxiang.evidence.vo.AgentConversationSummaryVO;

import java.util.List;
import java.util.Map;
import java.util.Optional;

public interface AgentRuntimeStateAdapter {

    /**
     * 从短期热态缓存读取上下文，结构不完整时返回空。
     */
    Optional<AgentContextRestoreVO> readContext(String userId, String taskId);

    /**
     * 将 PostgreSQL 重建出的上下文回填到短期热态缓存。
     */
    void writeContext(AgentContextRestoreVO context, boolean terminal);

    /**
     * 更新最近消息热态索引，并同步刷新上下文元数据。
     */
    void updateMessage(String userId, String taskId, AgentChatMessageVO message, boolean terminal);

    /**
     * 保存摘要后更新 activeSummaryId 和压缩时间。
     */
    void updateSummary(String userId, String taskId, AgentConversationSummaryVO summary, boolean terminal);

    /**
     * 写入任务事件缓冲，供 SSE 重连恢复。
     */
    void appendSseEvent(String taskId, Map<String, Object> event);

    /**
     * 读取最近任务事件缓冲。
     */
    List<Map<String, Object>> readSseEvents(String taskId);

    /**
     * 延长任务热态 TTL。
     */
    void refreshTaskTtl(String userId, String taskId, boolean terminal);

    /**
     * 当前 adapter 是否可用，供 diagnostics 展示。
     */
    boolean enabled();
}
