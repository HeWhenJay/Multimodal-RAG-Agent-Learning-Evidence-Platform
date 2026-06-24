package com.itxiang.evidence.vo;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

@Data
@Builder
public class AgentTaskDetailVO {

    private String id;
    private String taskType;
    private String status;
    private String title;
    private Map<String, Object> input;
    private Map<String, Object> plan;
    private Map<String, Object> draft;
    @JsonProperty("final")
    private Map<String, Object> finalResult;
    private String pythonThreadId;
    private String errorCode;
    private String errorMessage;
    private List<AgentToolCallVO> toolCalls;
    private List<AgentHumanReviewVO> reviews;
    private List<AgentOperationVO> operations;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
