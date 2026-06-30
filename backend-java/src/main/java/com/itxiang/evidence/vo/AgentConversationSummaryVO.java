package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AgentConversationSummaryVO {

    private String id;
    private String taskId;
    private String summaryType;
    private String coveredMessageStartId;
    private String coveredMessageEndId;
    private Integer coveredMessageCount;
    private Integer rawTokenEstimate;
    private Integer compressedTokenEstimate;
    private Map<String, Object> summary;
    private String summaryText;
    private List<Map<String, Object>> keyFacts;
    private List<Map<String, Object>> evidenceRefs;
    private String compressionModel;
    private String compressionPromptVersion;
    private Integer compressionVersion;
    private String status;
    private Map<String, Object> diagnostics;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
