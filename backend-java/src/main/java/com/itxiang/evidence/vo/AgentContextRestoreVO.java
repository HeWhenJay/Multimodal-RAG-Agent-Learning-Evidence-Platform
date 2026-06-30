package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

import java.util.List;
import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AgentContextRestoreVO {

    private String taskId;
    private String userId;
    private List<AgentChatMessageVO> messageWindow;
    private List<AgentChatMessageVO> compressionCandidateMessages;
    private List<AgentConversationSummaryVO> activeSummaries;
    private List<AgentConversationSummaryVO> summarySegments;
    private Map<String, Object> budgetMetadata;
}
